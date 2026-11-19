import copy
import json
import secrets
import uuid
import hashlib
import hmac
from datetime import timedelta

from django.db import transaction
from django.conf import settings
from django.utils import timezone

from researchloop.interaction.errors import Problem
from researchloop.participation.models import (
    EvidenceClock,
    Operation,
    ParticipantSession,
    Response,
    StartBucket,
    StartIntent,
)
from researchloop.participation.projections import (
    final_data,
    initial_state,
    session_data,
)
from researchloop.participation import sort_operations, sort_state, tree_state
from researchloop.studies.models import PublishedVersion, Study
from researchloop.studies.services import digest
from researchloop.trees import services as trees
from researchloop.governance.services import check_suppression


def bump(version):
    clock, _ = EvidenceClock.objects.get_or_create(version=version)
    clock.revision += 1
    clock.save(update_fields=["revision"])


@transaction.atomic
def throttle(source):
    now = timezone.now()
    source_key = hmac.new(
        settings.SECRET_KEY.encode(),
        ("start-source:" + source).encode(),
        hashlib.sha256,
    ).hexdigest()
    expired = list(
        StartBucket.objects.filter(window__lt=now - timedelta(minutes=10))
        .exclude(pk=source_key)
        .values_list("pk", flat=True)[:100]
    )
    StartBucket.objects.filter(
        pk__in=expired, window__lt=now - timedelta(minutes=10)
    ).delete()
    bucket, _ = StartBucket.objects.get_or_create(
        digest=source_key, defaults={"window": now}
    )
    bucket = StartBucket.objects.select_for_update().get(pk=bucket.pk)
    if now - bucket.window >= timedelta(minutes=10):
        bucket.window = now
        bucket.attempts = 0
    if bucket.attempts >= 30:
        raise Problem("RATE_LIMITED", 429)
    bucket.attempts += 1
    bucket.save()


def start(token, data, source, key=None, attempt_accounted=False):
    if not attempt_accounted:
        throttle(source)
    with transaction.atomic():
        return allocate(token, data, source, key)


def allocate(token, data, source, key=None):
    if data != {"accepted": True}:
        raise Problem("CONSENT_REQUIRED", 403)
    key = key or secrets.token_urlsafe(24)
    study = (
        Study.objects.select_for_update()
        .filter(recruitment_digest=digest(token))
        .first()
    )
    if study is None:
        raise Problem("NOT_AVAILABLE", 403)
    intent_digest = digest(token + ":" + key)
    intent = (
        StartIntent.objects.select_related("session__version__study")
        .filter(key_digest=intent_digest)
        .first()
    )
    if intent:
        check_subject(intent.session)
        return {
            **session_data(intent.session),
            "sessionCapability": session_token(intent.session.pk),
        }
    check_suppression(study.workspace_id)
    if study.state != "open" or study.current_version_id is None:
        raise Problem("NOT_AVAILABLE", 403)
    version = PublishedVersion.objects.select_for_update().get(
        pk=study.current_version_id
    )
    if version.state != "open":
        raise Problem("NOT_AVAILABLE", 403)
    if ParticipantSession.objects.filter(version=version).count() >= 1000:
        raise Problem("LIMIT_EXCEEDED", 409)
    session_id = uuid.uuid4()
    capability = session_token(session_id)
    session = ParticipantSession.objects.create(
        id=session_id,
        version=version,
        capability_digest=digest(capability),
        state=initial_state(version),
    )
    StartIntent.objects.create(key_digest=intent_digest, session=session)
    bump(version)
    return {**session_data(session), "sessionCapability": capability}


def session_token(session_id):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        ("participant:" + str(session_id)).encode(),
        hashlib.sha256,
    ).hexdigest()


def resolve(token, lock=False):
    session = (
        ParticipantSession.objects.select_related("version__study")
        .filter(capability_digest=digest(token))
        .first()
    )
    if session is None:
        raise Problem("SESSION_UNAVAILABLE", 410)
    if lock:
        Study.objects.select_for_update().get(pk=session.version.study_id)
        PublishedVersion.objects.select_for_update().get(pk=session.version_id)
        session = (
            ParticipantSession.objects.select_for_update()
            .select_related("version__study")
            .get(pk=session.pk)
        )
    return session


def writable(session):
    check_subject(session)
    now = timezone.now()
    version = session.version
    if version.state == "revoked":
        raise Problem("REVOKED", 423)
    deadline = session.activity_at + timedelta(days=7)
    if version.write_deadline:
        deadline = min(deadline, version.write_deadline)
    if now >= deadline:
        raise Problem("EXPIRED", 410)
    if session.status != "active":
        raise Problem("TERMINAL_SESSION", 409)


def check_subject(session):
    _, suppressed = check_suppression(session.version.study.workspace_id)
    if str(session.pk) in suppressed:
        raise Problem("SESSION_UNAVAILABLE", 410)


@transaction.atomic
def resume(token):
    session = resolve(token, True)
    check_subject(session)
    if (
        session.version.study.method == "tree-test-v2"
        and session.version.state == "revoked"
    ):
        raise Problem("REVOKED", 423)
    if session.status != "submitted":
        writable(session)
    return session_data(session)


@transaction.atomic
def resume_tree_v2(token):
    session = resolve(token, True)
    check_subject(session)
    if session.version.study.method != "tree-test-v2":
        raise Problem("SESSION_UNAVAILABLE", 410)
    if session.version.state == "revoked":
        raise Problem("REVOKED", 423)
    if session.status != "submitted":
        writable(session)
    view = session_data(session)
    view["state"] = view.pop("participantView")
    return view


def operate_tree_v2(token, data, key):
    return operate(token, data, key, tree_v2=True)


@transaction.atomic
def operate(token, data, key, *, tree_v2=False):
    session = resolve(token, True)
    writable(session)
    if tree_v2 != (session.version.study.method == "tree-test-v2"):
        raise Problem("SESSION_UNAVAILABLE", 410)
    if (
        set(data) != {"sequence", "operationKind", "payload"}
        or type(data["sequence"]) is not int
        or not isinstance(data["operationKind"], str)
        or not isinstance(data["payload"], dict)
    ):
        raise Problem("INVALID_OPERATION")
    fingerprint = digest(json.dumps(data, sort_keys=True, separators=(",", ":")))
    prior = Operation.objects.filter(session=session, operation_id=key).first()
    if prior:
        if prior.digest != fingerprint:
            raise Problem("IDEMPOTENCY_CONFLICT", 409)
        return prior.acknowledgement
    if data["sequence"] != session.revision + 1:
        raise Problem("STALE_SEQUENCE", 409, revision=session.revision)
    inverse, undone = None, None
    if session.version.study.method == "card-sort":
        session.state, inverse, undone = apply_sort(session, data, key)
    elif tree_v2:
        session.state = tree_state.apply(
            copy.deepcopy(session.state), session.version.content, data
        )
    else:
        session.state = trees.apply(
            copy.deepcopy(session.state), session.version.content, data
        )
    session.revision += 1
    session.activity_at = timezone.now()
    session.save(update_fields=["state", "revision", "activity_at"])
    acknowledgement = {
        "sequence": data["sequence"],
        "revision": session.revision,
        "saveState": "saved",
    }
    operation = Operation.objects.create(
        session=session,
        operation_id=key,
        sequence=data["sequence"],
        digest=fingerprint,
        payload=copy.deepcopy(data),
        acknowledgement=acknowledgement,
        prior_revision=session.revision - 1,
        resulting_revision=session.revision,
        inverse_data=inverse,
    )
    if undone is not None:
        undone.undone_by = operation
        undone.save(update_fields=["undone_by"])
    bump(session.version)
    return acknowledgement


def apply_sort(session, data, key):
    if data["operationKind"] != "undo":
        state, inverse = sort_operations.apply(
            session.state, session.version.content, data, key
        )
        return state, inverse, None
    sort_operations.fields(data["payload"], {"operationId"})
    prior = Operation.objects.filter(
        session=session, operation_id=data["payload"]["operationId"]
    ).first()
    if (
        prior is None
        or prior.undone_by_id is not None
        or prior.inverse_data is None
        or prior.resulting_revision != session.revision
    ):
        raise Problem("UNDO_CONFLICT", 409)
    state = sort_state.ordered_snapshot(session.state, session.version.content)
    if sort_operations.state_digest(state) != prior.inverse_data["resultDigest"]:
        raise Problem("UNDO_CONFLICT", 409)
    restored = copy.deepcopy(prior.inverse_data["priorState"])
    sort_state.validate_snapshot(restored, session.version.content)
    return restored, None, prior


@transaction.atomic
def finalize(token, data, key):
    session = resolve(token, True)
    if set(data) != {"expectedRevision"} or type(data["expectedRevision"]) is not int:
        raise Problem("INVALID_REQUEST")
    existing = Response.objects.filter(session=session).first()
    if existing:
        check_subject(session)
        if session.version.study.method == "tree-test-v2":
            if session.version.state == "revoked":
                raise Problem("REVOKED", 423)
            if data["expectedRevision"] != existing.revision:
                raise Problem("IDEMPOTENCY_CONFLICT", 409)
        if existing.finalization_key != key:
            raise Problem("TERMINAL_SESSION", 409)
        return final_data(existing)
    writable(session)
    if data["expectedRevision"] != session.revision:
        raise Problem("STALE_REVISION", 409, revision=session.revision)
    method = sort_state if session.version.study.method == "card-sort" else trees
    if not method.complete(session.state, session.version.content):
        raise Problem("INCOMPLETE_RESPONSE")
    response = Response.objects.create(
        session=session,
        version=session.version,
        state=copy.deepcopy(session.state),
        revision=session.revision,
        finalization_key=key,
    )
    session.status = "submitted"
    session.save(update_fields=["status"])
    bump(session.version)
    return final_data(response)
