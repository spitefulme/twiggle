"""One bounded local worker, persisted admissions and non-upserting fenced publication."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
import time
import uuid
import logging

from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone

from researchloop.analysis import ports, services
from researchloop.analysis.models import (
    AnalysisCohort,
    AnalysisJob,
    AnalysisQueueGuard,
    AnalysisRequestReceipt,
    AnalysisResult,
    AnnotationState,
    WorkerHeartbeat,
)
from researchloop.governance.gate import delivery_gate
from researchloop.governance.services import analysis_guard
from researchloop.interaction.errors import Problem
from researchloop.identity.services import authorize_actor

ACTIVE = ["queued", "running"]
HEARTBEAT_SECONDS = 3
LEASE_SECONDS = 3
logger = logging.getLogger(__name__)


@transaction.atomic
def heartbeat(identity, active=True):
    identity = uuid.UUID(str(identity))
    now = timezone.now()
    row, _ = WorkerHeartbeat.objects.get_or_create(
        pk=1,
        defaults={
            "identity": identity,
            "process_id": os.getpid(),
            "seen_at": now,
            "active": active,
        },
    )
    row = WorkerHeartbeat.objects.select_for_update().get(pk=1)
    if row.identity != identity:
        if not active:
            return False
        if row.active and row.seen_at > now - timedelta(seconds=HEARTBEAT_SECONDS):
            raise Problem("ANALYSIS_WORKER_BUSY", 503)
    row.identity, row.process_id, row.seen_at, row.active = (
        identity,
        os.getpid(),
        now,
        active,
    )
    row.save(update_fields=["identity", "process_id", "seen_at", "active"])
    return True


def worker_available(now=None):
    now = now or timezone.now()
    return WorkerHeartbeat.objects.filter(
        pk=1, active=True, seen_at__gt=now - timedelta(seconds=HEARTBEAT_SECONDS)
    ).exists()


def _strict_transaction():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '500ms'")
        cursor.execute("SET LOCAL statement_timeout = '750ms'")


def expire_due():
    candidates = list(
        AnalysisJob.objects.filter(
            status__in=ACTIVE, deadline_at__lte=timezone.now()
        ).values_list("pk", "workspace_id")[:64]
    )
    for job_id, workspace_id in candidates:
        try:
            with delivery_gate(workspace_id, timeout_ms=500), transaction.atomic():
                _strict_transaction()
                analysis_guard(workspace_id, allow_barrier=True)
                AnalysisJob.objects.filter(
                    pk=job_id, status__in=ACTIVE, deadline_at__lte=timezone.now()
                ).update(
                    status="failed",
                    error_code="ANALYSIS_TIMEOUT",
                    completed_at=timezone.now(),
                    lease_expires_at=None,
                )
        except Problem as error:
            # An unavailable authority does not grant capacity or publication.
            logger.warning("Analysis expiry authority unavailable: %s", error.code)


def _ack(job, public):
    if public.method == "tree-test-v2":
        status_location = (
            f"/api/v1/studies/{public.study_id}/versions/{job.version_id}"
            f"/tree-v2/analysis-jobs?jobId={job.pk}"
        )
    else:
        status_location = (
            f"/api/v1/studies/{public.study_id}/analysis-jobs/{job.pk}"
            f"?versionId={job.version_id}"
        )
    return {
        "jobId": str(job.pk),
        "versionId": str(job.version_id),
        "cohortId": str(job.cohort_id),
        "state": job.status,
        "progress": job.progress,
        "errorCode": job.error_code or None,
        "cancelRequested": job.cancel_requested,
        "statusLocation": status_location,
    }


def request(user, study_id, version_id, key, metric="immediate"):
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 128
        or metric not in {"immediate", "ancestor", "tree-v2"}
    ):
        raise Problem("INVALID_ANALYSIS_INPUT")
    public = ports.authorized_version(user, study_id, version_id)
    if public.method != ("tree-test-v2" if metric == "tree-v2" else "card-sort"):
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    admitted_at = timezone.now()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"versionId": str(public.version_id), "metric": metric}, sort_keys=True
        ).encode()
    ).hexdigest()
    expire_due()
    with delivery_gate(public.workspace_id), transaction.atomic():
        generation, _ = analysis_guard(public.workspace_id)
        prior = (
            AnalysisRequestReceipt.objects.filter(
                actor_id=user.pk, version_id=public.version_id, key=key
            )
            .select_related("job__cohort")
            .first()
        )
        if prior:
            if prior.digest != fingerprint:
                raise Problem("IDEMPOTENCY_CONFLICT", 409)
            if not prior.acknowledgement:
                raise Problem("ANALYSIS_RECEIPT_RECONCILIATION_REQUIRED", 503)
            return prior.acknowledgement
        if metric == "tree-v2":
            from researchloop.analysis.tree_projection import capture

            cohort = capture(user, study_id, version_id)
        else:
            cohort = services.capture(user, study_id, version_id, metric)
        AnalysisQueueGuard.objects.get_or_create(pk=1)
        AnalysisQueueGuard.objects.select_for_update().get(pk=1)
        existing = (
            AnalysisJob.objects.filter(
                cohort=cohort,
                deletion_generation=generation,
                status__in=[*ACTIVE, "succeeded"],
            )
            .select_related("cohort")
            .order_by("admitted_at")
            .first()
        )
        if existing:
            job = existing
        else:
            if not worker_available():
                raise Problem("ANALYSIS_WORKER_UNAVAILABLE", 503)
            if (
                AnalysisJob.objects.filter(
                    status__in=ACTIVE, workspace_id=public.workspace_id
                ).count()
                >= 32
                or AnalysisJob.objects.filter(status__in=ACTIVE).count() >= 64
            ):
                raise Problem("ANALYSIS_QUEUE_BUSY", 503)
            if timezone.now() >= admitted_at + timedelta(seconds=30):
                raise Problem("ANALYSIS_TIMEOUT", 503)
            job = AnalysisJob.objects.create(
                workspace_id=public.workspace_id,
                actor_id=user.pk,
                version_id=public.version_id,
                cohort=cohort,
                admitted_at=admitted_at,
                deadline_at=admitted_at + timedelta(seconds=30),
                deletion_generation=generation,
                input_revision=cohort.fingerprint,
            )
        acknowledgement = _ack(job, public)
        AnalysisRequestReceipt.objects.create(
            actor_id=user.pk,
            version_id=public.version_id,
            key=key,
            digest=fingerprint,
            job=job,
            acknowledgement=acknowledgement,
        )
        return acknowledgement


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    workspace_id: int
    cohort_id: uuid.UUID
    owner: uuid.UUID
    attempt: int
    fence: int
    generation: int
    input_revision: str
    deadline_at: object


def claim(identity, *, workspace_only=None):
    identity = uuid.UUID(str(identity))
    heartbeat(identity)
    now = timezone.now()
    eligible = AnalysisJob.objects.filter(
        Q(status="queued") | Q(status="running", lease_expires_at__lte=now)
    )
    if workspace_only is not None:
        eligible = eligible.filter(workspace_id=workspace_only)
    candidates = list(
        eligible.order_by("admitted_at").values_list("pk", "workspace_id")[:64]
    )
    for job_id, workspace_id in candidates:
        with delivery_gate(workspace_id, timeout_ms=500), transaction.atomic():
            _strict_transaction()
            generation, _ = analysis_guard(workspace_id)
            job = (
                AnalysisJob.objects.select_for_update(skip_locked=True)
                .filter(pk=job_id)
                .first()
            )
            if job is None or job.status not in ACTIVE:
                continue
            now = timezone.now()
            if (
                job.deadline_at <= now
                or job.deletion_generation != generation
                or job.cancel_requested
            ):
                job.status = (
                    "cancelled"
                    if job.cancel_requested or job.deletion_generation != generation
                    else "failed"
                )
                job.error_code = (
                    "ANALYSIS_FENCED"
                    if job.deletion_generation != generation
                    else "ANALYSIS_CANCELLED"
                    if job.cancel_requested
                    else "ANALYSIS_TIMEOUT"
                )
                job.completed_at = now
                job.save(update_fields=["status", "error_code", "completed_at"])
                continue
            if job.status == "running" and job.lease_expires_at > now:
                continue
            job.status, job.lease_owner = "running", identity
            job.attempt += 1
            job.fence += 1
            job.progress = 5
            job.lease_expires_at = min(
                job.deadline_at, now + timedelta(seconds=LEASE_SECONDS)
            )
            job.save(
                update_fields=[
                    "status",
                    "lease_owner",
                    "attempt",
                    "fence",
                    "progress",
                    "lease_expires_at",
                ]
            )
            return Claim(
                job.pk,
                workspace_id,
                job.cohort_id,
                identity,
                job.attempt,
                job.fence,
                generation,
                job.input_revision,
                job.deadline_at,
            )
    return None


def _checked(claimed, *, renew=True):
    analysis_guard(claimed.workspace_id, expected_generation=claimed.generation)
    job = AnalysisJob.objects.select_for_update().filter(pk=claimed.job_id).first()
    now = timezone.now()
    if (
        job is None
        or job.status != "running"
        or job.lease_owner != claimed.owner
        or job.attempt != claimed.attempt
        or job.fence != claimed.fence
        or job.input_revision != claimed.input_revision
        or job.deletion_generation != claimed.generation
    ):
        raise Problem("ANALYSIS_FENCED", 409)
    if job.cancel_requested:
        raise Problem("ANALYSIS_CANCELLED", 409)
    if now >= job.deadline_at:
        raise Problem("ANALYSIS_TIMEOUT", 503)
    if job.lease_expires_at is None or now >= job.lease_expires_at:
        raise Problem("ANALYSIS_FENCED", 409)
    authorize_actor(job.actor_id, claimed.workspace_id)
    annotation = AnnotationState.objects.filter(version_id=job.version_id).first()
    # The fence needs one scalar, not a materialized immutable cohort snapshot
    # (up to1000 responses) on every bounded cancellation/authority check.
    captured_annotation = AnalysisCohort.objects.values_list(
        "annotation_revision", flat=True
    ).get(pk=job.cohort_id)
    if annotation and annotation.revision != captured_annotation:
        raise Problem("COHORT_STALE", 409)
    worker = (
        WorkerHeartbeat.objects.select_for_update()
        .filter(pk=1, identity=claimed.owner, active=True)
        .first()
    )
    if worker is None:
        raise Problem("ANALYSIS_FENCED", 409)
    if renew:
        worker.seen_at = now
        worker.save(update_fields=["seen_at"])
        job.lease_expires_at = min(
            job.deadline_at, now + timedelta(seconds=LEASE_SECONDS)
        )
        job.progress = min(95, job.progress + 5)
        job.save(update_fields=["lease_expires_at", "progress"])
    return job


def _failed(claimed, code):
    try:
        with delivery_gate(claimed.workspace_id, timeout_ms=500), transaction.atomic():
            _strict_transaction()
            analysis_guard(claimed.workspace_id, allow_barrier=True)
            AnalysisJob.objects.filter(
                pk=claimed.job_id,
                status="running",
                lease_owner=claimed.owner,
                attempt=claimed.attempt,
                fence=claimed.fence,
            ).update(
                status="cancelled"
                if code in {"ANALYSIS_FENCED", "ANALYSIS_CANCELLED"}
                else "failed",
                error_code=code,
                completed_at=timezone.now(),
                lease_expires_at=None,
            )
    except Exception as error:
        # Authority/storage failure never creates a replacement row or result.
        logger.error(
            "Analysis failure could not be persisted: %s", type(error).__name__
        )
        return False
    return True


def execute(claimed, identity):
    if claimed is None:
        return False
    if uuid.UUID(str(identity)) != claimed.owner:
        return False
    last_check = 0
    local_deadline = time.monotonic() + max(
        0, (claimed.deadline_at - timezone.now()).total_seconds()
    )

    def check(force=False):
        nonlocal last_check
        now = time.monotonic()
        if now >= local_deadline:
            raise Problem("ANALYSIS_TIMEOUT", 503)
        if not force and now - last_check < 0.25:
            return
        with delivery_gate(claimed.workspace_id, timeout_ms=500), transaction.atomic():
            _strict_transaction()
            _checked(claimed)
        last_check = time.monotonic()

    try:
        check(True)
        cohort = AnalysisCohort.objects.get(pk=claimed.cohort_id)
        if cohort.fingerprint != claimed.input_revision:
            raise Problem("ANALYSIS_FENCED", 409)
        payload = services.calculate(cohort, check=check)
        with delivery_gate(claimed.workspace_id, timeout_ms=500), transaction.atomic():
            _strict_transaction()
            job = _checked(claimed)
            if AnalysisResult.objects.filter(cohort=cohort).exists():
                raise Problem("ANALYSIS_FENCED", 409)
            AnalysisResult.objects.create(cohort=cohort, payload=payload)
            if timezone.now() >= job.deadline_at:
                raise Problem("ANALYSIS_TIMEOUT", 503)
            job.status, job.progress, job.completed_at, job.lease_expires_at = (
                "succeeded",
                100,
                timezone.now(),
                None,
            )
            job.save(
                update_fields=["status", "progress", "completed_at", "lease_expires_at"]
            )
        return True
    except Problem as error:
        logger.warning("Analysis job failed: %s", error.code)
        _failed(claimed, error.code)
        return False
    except Exception as error:
        logger.error("Analysis computation failed: %s", type(error).__name__)
        _failed(claimed, "ANALYSIS_FAILED")
        return False


def run_once(identity):
    claimed = claim(identity)
    if claimed is None:
        return None
    execute(claimed, identity)
    return claimed.job_id


def status(user, study_id, version_id, job_id):
    public = ports.authorized_version(user, study_id, version_id)
    with delivery_gate(public.workspace_id), transaction.atomic():
        generation, _ = analysis_guard(public.workspace_id)
        job = (
            AnalysisJob.objects.filter(
                pk=job_id,
                version_id=public.version_id,
                workspace_id=public.workspace_id,
            )
            .select_related("cohort")
            # Keep the governance row lock, but never detoast the immutable
            # full response snapshot just to report scalar job status.
            .only(
                "id",
                "version_id",
                "status",
                "progress",
                "error_code",
                "cancel_requested",
                "deletion_generation",
                "deadline_at",
                "lease_expires_at",
                "cohort__annotation_revision",
            )
            .first()
        )
        if job is None:
            raise Problem("NOT_FOUND", 404)
        if job.deletion_generation != generation:
            raise Problem("COHORT_STALE", 409)
        state = AnnotationState.objects.filter(version_id=public.version_id).first()
        if state and state.revision != job.cohort.annotation_revision:
            raise Problem("COHORT_STALE", 409)
        payload = _ack(job, public)
        if job.status in ACTIVE:
            if job.deadline_at <= timezone.now():
                payload.update(state="failed", errorCode="ANALYSIS_TIMEOUT")
            elif not worker_available() or (
                job.status == "running"
                and (
                    job.lease_expires_at is None
                    or job.lease_expires_at <= timezone.now()
                )
            ):
                payload.update(
                    state="stalled",
                    errorCode="ANALYSIS_WORKER_UNAVAILABLE",
                    retryAfter=1,
                )
        return payload


def result(user, study_id, version_id, job_id):
    payload = status(user, study_id, version_id, job_id)
    if payload["state"] != "succeeded":
        raise Problem("ANALYSIS_NOT_READY", 503)
    public = ports.authorized_version(user, study_id, version_id)
    with delivery_gate(public.workspace_id), transaction.atomic():
        generation, _ = analysis_guard(public.workspace_id)
        job = (
            AnalysisJob.objects.filter(
                pk=job_id,
                version_id=public.version_id,
                workspace_id=public.workspace_id,
                status="succeeded",
                deletion_generation=generation,
            )
            .select_related("cohort")
            .first()
        )
        if job is None:
            raise Problem("COHORT_STALE", 409)
        state = AnnotationState.objects.filter(version_id=public.version_id).first()
        if state and state.revision != job.cohort.annotation_revision:
            raise Problem("COHORT_STALE", 409)
        stored = AnalysisResult.objects.filter(cohort=job.cohort).first()
        if stored is None:
            raise Problem("ANALYSIS_NOT_READY", 503)
        return stored.payload


def latest(user, study_id, version_id, metric="immediate"):
    public = ports.authorized_version(user, study_id, version_id)
    if public.method != ("tree-test-v2" if metric == "tree-v2" else "card-sort"):
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    with delivery_gate(public.workspace_id), transaction.atomic():
        generation, _ = analysis_guard(public.workspace_id)
        annotation = AnnotationState.objects.filter(
            version_id=public.version_id
        ).first()
        job = (
            AnalysisJob.objects.filter(
                version_id=public.version_id,
                workspace_id=public.workspace_id,
                deletion_generation=generation,
                cohort__annotation_revision=annotation.revision if annotation else 0,
                cohort__snapshot__metric=metric,
            )
            .order_by("-admitted_at")
            .first()
        )
        if job is None:
            raise Problem("ANALYSIS_NOT_READY", 503)
        return str(job.pk)


def cancel(user, study_id, version_id, job_id, key):
    public = ports.authorized_version(user, study_id, version_id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "operation": "cancel",
                "versionId": str(public.version_id),
                "jobId": str(job_id),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with delivery_gate(public.workspace_id), transaction.atomic():
        analysis_guard(public.workspace_id)
        job = (
            AnalysisJob.objects.select_for_update()
            .filter(
                pk=job_id,
                workspace_id=public.workspace_id,
                version_id=public.version_id,
            )
            .select_related("cohort")
            .first()
        )
        if job is None:
            raise Problem("NOT_FOUND", 404)
        prior = AnalysisRequestReceipt.objects.filter(
            actor_id=user.pk, version_id=public.version_id, key=key
        ).first()
        if prior:
            if prior.digest != fingerprint:
                raise Problem("IDEMPOTENCY_CONFLICT", 409)
            if not prior.acknowledgement:
                raise Problem("ANALYSIS_RECEIPT_RECONCILIATION_REQUIRED", 503)
            return prior.acknowledgement
        if job.status in ACTIVE:
            job.cancel_requested = True
            if job.status == "queued":
                job.status, job.error_code, job.completed_at = (
                    "cancelled",
                    "ANALYSIS_CANCELLED",
                    timezone.now(),
                )
            job.save(
                update_fields=[
                    "cancel_requested",
                    "status",
                    "error_code",
                    "completed_at",
                ]
            )
        acknowledgement = _ack(job, public)
        AnalysisRequestReceipt.objects.create(
            actor_id=user.pk,
            version_id=public.version_id,
            key=key,
            digest=fingerprint,
            job=job,
            acknowledgement=acknowledgement,
        )
        return acknowledgement
