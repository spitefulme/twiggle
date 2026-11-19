import hashlib
import secrets
import uuid
from contextlib import ExitStack
from datetime import timedelta
from time import monotonic

from django.db import connection, models, transaction
from django.utils import timezone

from researchloop.analysis import delivery, ports
from researchloop.evidence_delivery import formats, handoff, intents, selection, storage
from researchloop.evidence_delivery.models import DownloadGrant, Export, Selection
from researchloop.governance.readiness import assert_fresh_local
from researchloop.identity.services import resolve_authorized_actor
from researchloop.governance.gate import delivery_gate
from researchloop.interaction.errors import Problem


FORMATS = {"json", "csv", "svg"}
ARTIFACT_LIFETIME = timedelta(minutes=5)
MAX_CONCURRENT_GRANTS = 32


def available_until(job):
    return job.expires_at + ARTIFACT_LIFETIME


def payload(job, capability=None):
    state = job.state
    return {
        "jobId": str(job.pk),
        "state": state,
        "status": state if state in {"queued", "running", "completed"} else "failed",
        "safeErrorCode": "EXPORT_UNAVAILABLE"
        if state in {"failed", "revoked", "expired"}
        else None,
        "versionId": str(job.selection.version_id),
        "cohortId": str(job.selection.cohort_id),
        "format": job.format,
        "downloadCapability": capability,
    }


def admit(user, study, data, selector, key, *, resources=None):
    if (
        set(data) != {"versionId", "cohortId", "format"}
        or data["format"] not in FORMATS
    ):
        raise Problem("INVALID_REQUEST", 422)
    version, cohort = data["versionId"], data["cohortId"]
    public = ports.authorized_version(user, study, version)
    with handoff.scope(public.workspace_id, resources), transaction.atomic():
        if selector:
            selected = Selection.objects.filter(
                pk=selector,
                actor_id=user.pk,
                workspace_id=public.workspace_id,
                study_id=study,
                version_id=version,
                cohort_id=cohort,
            ).first()
            if selected is None:
                raise Problem("COHORT_STALE", 409)
            selection.current(user, selected, hydrate=False)
        else:
            proof = delivery.from_result(user, study, version, cohort_id=cohort)
            selected = selection.capture(user, proof)
        scope = "export:" + study
        receipt, intent = intents.check(user.pk, scope, key, [data, selector])
        if receipt:
            return receipt.acknowledgement
        job = Export.objects.create(
            actor_id=user.pk,
            selection=selected,
            format=data["format"],
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        answer = payload(job)
        intents.remember(user.pk, scope, key, intent, job, answer)
        return answer


def digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def status(user, job_id, *, resources=None):
    job = (
        Export.objects.select_related("selection")
        .filter(pk=job_id, actor_id=user.pk)
        .first()
    )
    if job is None:
        raise Problem("NOT_FOUND", 404)
    with handoff.scope(job.selection.workspace_id, resources), transaction.atomic():
        job = (
            Export.objects.select_for_update(of=("self",))
            .select_related("selection")
            .get(pk=job.pk)
        )
        selection.current(user, job.selection, hydrate=False)
        if job.expires_at <= timezone.now() and job.state in {"queued", "running"}:
            job.state = "failed"
        token = None
        if job.state == "completed":
            now = timezone.now()
            until = available_until(job)
            if until <= now:
                job.state = "expired"
            else:
                if (
                    DownloadGrant.objects.filter(job=job, expires_at__gt=now).count()
                    >= MAX_CONCURRENT_GRANTS
                ):
                    raise Problem("DOWNLOAD_GRANT_LIMIT", 503)
                token = secrets.token_hex(32)
                DownloadGrant.objects.create(
                    digest=digest(token), job=job, expires_at=until
                )
        return payload(job, token)


def _claim(workspace_id):
    now = timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(17102, 1)")
        own = Export.objects.filter(selection__workspace_id=workspace_id)
        own.filter(state__in=["queued", "running"], expires_at__lte=now).update(
            state="failed"
        )
        if Export.objects.filter(state="running", lease_until__gt=now).exists():
            return None
        job = (
            own.select_for_update(skip_locked=True, of=("self",))
            .filter(state__in=["queued", "running"])
            .order_by("created_at", "pk")
            .first()
        )
        if job:
            job.state = "running"
            job.lease_id = uuid.uuid4()
            job.lease_until = job.expires_at
            job.save(update_fields=["state", "lease_id", "lease_until"])
        return job


def run_once(workspace_id):
    assert_fresh_local()
    with storage.render_slot() as owned:
        if not owned:
            return False
        with storage.root_writer_lock() as has_storage:
            if not has_storage:
                return False
            _sweep(workspace_id)
            job = _claim(workspace_id)
            if job is None:
                return False
            _render(job)
            return True


def _sweep(workspace_id):
    now = timezone.now()
    DownloadGrant.objects.filter(
        job__selection__workspace_id=workspace_id,
        expires_at__lt=now - ARTIFACT_LIFETIME,
    ).delete()
    with delivery_gate(workspace_id), transaction.atomic():
        targets = (
            Export.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(selection__workspace_id=workspace_id, lease_id__isnull=False)
            .filter(
                models.Q(state="completed", expires_at__lte=now - ARTIFACT_LIFETIME)
                | models.Q(state__in=["failed", "running"], expires_at__lte=now)
            )
            .order_by("expires_at")[:25]
        )
        for job in targets:
            storage.remove_job_paths(job)
            job.state = "expired" if job.state == "completed" else "failed"
            job.lease_id = None
            job.artifact_size = 0
            job.artifact_digest = ""
            job.save(
                update_fields=["state", "lease_id", "artifact_size", "artifact_digest"]
            )


def _render(job):
    path = None
    try:
        selected = job.selection
        remaining = max(0, (job.expires_at - timezone.now()).total_seconds())
        ends_at = monotonic() + remaining

        def deadline():
            if monotonic() >= ends_at:
                raise Problem("EXPORT_TIMEOUT", 503)

        with ExitStack() as file_scope:
            with delivery_gate(selected.workspace_id), transaction.atomic():
                user = resolve_authorized_actor(job.actor_id, selected.workspace_id)
                selected.refresh_from_db()
                proof = selection.current(user, selected)
                owned = Export.objects.select_for_update().get(pk=job.pk)
                deadline()
                if owned.state != "running" or owned.lease_id != job.lease_id:
                    raise Problem("EXPORT_LEASE_STALE", 503)
                path, opened, available = storage.open_stage(job.pk, job.lease_id)
                stream = file_scope.enter_context(opened)
            size, file_digest = storage.write_open(
                stream, available, formats.chunks(job, proof), deadline
            )
        with delivery_gate(selected.workspace_id), transaction.atomic():
            user = resolve_authorized_actor(job.actor_id, selected.workspace_id)
            selected.refresh_from_db()
            selection.current(user, selected, hydrate=False)
            owned = Export.objects.select_for_update().get(pk=job.pk)
            deadline()
            if owned.state != "running" or owned.lease_id != job.lease_id:
                raise Problem("EXPORT_LEASE_STALE", 503)
            path = storage.finalized(path, job.pk, job.lease_id)
            deadline()
            owned.state = "completed"
            owned.artifact_digest = file_digest
            owned.artifact_size = size
            owned.save(update_fields=["state", "artifact_digest", "artifact_size"])
        path = None
    except (Problem, OSError, Export.DoesNotExist, Selection.DoesNotExist):
        # A completed erasure may remove both rows while a worker still holds
        # its old in-memory lease. Treat missing ownership as terminal rather
        # than stopping the worker or recreating any delivery state.
        Export.objects.filter(pk=job.pk, state="running", lease_id=job.lease_id).update(
            state="failed"
        )
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
