"""Scoped erasure admission and restartable internal owner dispatch."""

from calendar import monthrange
from contextlib import ExitStack
from datetime import timedelta, timezone as datetime_timezone
import hmac
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from researchloop.governance import authority
from researchloop.governance.gate import delivery_gate, maintenance_gate
from researchloop.governance.models import (
    ErasureAttempt,
    ErasureCase,
    ErasureCommand,
    Ledger,
    Suppression,
)
from researchloop.governance.services import record_audit
from researchloop.identity.services import lock_administrator
from researchloop.interaction.errors import Problem
from researchloop.participation.models import ParticipantSession, Response
from researchloop.studies.models import PublishedVersion, Study


OWNERS = ("U2", "U3", "U4")
METHODS = {"U2": ("card-sort",), "U3": ("tree-test", "tree-test-v2")}
MAX_ATTEMPTS = 5


def ledger_for_update(workspace_id):
    row, _ = Ledger.objects.get_or_create(workspace_id=workspace_id)
    row = Ledger.objects.select_for_update().get(pk=row.pk)
    if not row.available:
        raise authority.Unavailable()
    return row


def project(ledger, journal, *, quarantined=False):
    """Exact replay is idempotent; it never clears an existing suppression."""
    for fact in journal.entries:
        case, created = ErasureCase.objects.get_or_create(
            pk=fact["id"],
            defaults={
                "workspace_id": ledger.workspace_id,
                "kind": fact["kind"],
                "subject": fact["subject"],
                "study_id": fact["study"],
            },
        )
        if (case.workspace_id, case.kind, str(case.subject), str(case.study_id)) != (
            ledger.workspace_id,
            fact["kind"],
            fact["subject"],
            fact["study"],
        ):
            raise authority.Unavailable()
        if created:
            record_audit(fact["actor"], "erasure-admitted", fact["id"])
        if fact["kind"] == "study":
            scope = Study.objects.filter(
                pk=fact["study"], workspace_id=ledger.workspace_id
            )
            scope.update(state="deleted")
            PublishedVersion.objects.filter(study__in=scope).update(
                state="revoked", write_deadline=timezone.now()
            )
            if not case.lifecycle_closed:
                case.lifecycle_closed = True
                case.save(update_fields=["lifecycle_closed"])
        for subject in fact["sessions"]:
            Suppression.objects.get_or_create(ledger=ledger, subject=subject)
        for owner in OWNERS:
            ErasureCommand.objects.get_or_create(
                case=case, epoch=journal.epoch, owner=owner
            )
        if quarantined and case.status != "accepted":
            case.status = "accepted"
            case.save(update_fields=["status"])
    if ledger.u5_head != journal.head:
        ledger.revision += 1
        ledger.analysis_generation += 1
    ledger.u5_head = journal.head
    ledger.u5_epoch = journal.epoch
    ledger.u5_quarantined = quarantined
    ledger.save(
        update_fields=[
            "revision",
            "analysis_generation",
            "u5_head",
            "u5_epoch",
            "u5_quarantined",
        ]
    )


def _reconcile_successor(ledger, journal):
    """Replay exactly one authenticated append, never an epoch transition."""
    if (
        ledger.u5_quarantined
        or journal.restore_state == "pending"
        or ledger.u5_epoch != journal.epoch
        or not ledger.u5_head
        or not journal.entries
    ):
        raise authority.Unavailable()
    previous = authority.journal_value(
        ledger.workspace_id,
        journal.epoch,
        journal.entries[:-1],
        journal.manifest,
        restore_state=journal.restore_state,
        completion=journal.completion,
    )
    if not hmac.compare_digest(ledger.u5_head, authority.seal(previous)):
        raise authority.Unavailable()
    project(ledger, journal)


def _accepted(ledger, kind, subject, study_id, sessions, actor, journal):
    fact = next(
        (
            item
            for item in journal.entries
            if item["kind"] == kind and item["subject"] == str(subject)
        ),
        None,
    )
    if ledger.u5_head != journal.head:
        if fact is None or fact != journal.entries[-1]:
            raise authority.Unavailable()
        _reconcile_successor(ledger, journal)
    if fact is None:
        version_bindings = {str(session): str(version) for session, version in sessions}
        fact = dict(
            sequence=len(journal.entries) + 1,
            id=str(uuid4()),
            kind=kind,
            subject=str(subject),
            study=str(study_id),
            sessions=sorted(version_bindings),
            versions=version_bindings,
            actor=str(actor),
        )
        journal = authority.publish(
            ledger.workspace_id,
            journal.epoch,
            (*journal.entries, fact),
            journal.manifest,
            restore_state=journal.restore_state,
            completion=journal.completion,
        )
    if ledger.u5_head != journal.head:
        project(ledger, journal)
    return ErasureCase.objects.get(pk=fact["id"], workspace_id=ledger.workspace_id)


def admit_study(user, workspace_id, study_id, *, revision):
    if type(revision) is not int or revision < 1:
        raise Problem("INVALID_REQUEST", 422)
    with delivery_gate(workspace_id, True), transaction.atomic():
        lock_administrator(user, workspace_id)
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        if (
            ledger.u5_quarantined
            or journal.restore_state == "pending"
            or ledger.u5_epoch != journal.epoch
        ):
            raise authority.Unavailable()
        study = (
            Study.objects.select_for_update()
            .filter(pk=study_id, workspace_id=workspace_id)
            .first()
        )
        if study is None or study.draft.revision != revision:
            raise Problem(
                "NOT_FOUND" if study is None else "STALE_REVISION",
                404 if study is None else 409,
            )
        sessions = ParticipantSession.objects.filter(version__study=study).values_list(
            "pk", "version_id"
        )
        return _accepted(
            ledger, "study", study.pk, study.pk, sessions, user.pk, journal
        )


def expiry_cutoff(clock):
    clock = clock.astimezone(datetime_timezone.utc)
    return clock.replace(
        year=clock.year + 1,
        day=min(clock.day, monthrange(clock.year + 1, clock.month)[1]),
    )


def admit_expiry(workspace_id, session_id, *, revision, now=None):
    """Internal retention worker entry point, never a public API."""
    now = now or timezone.now()
    with delivery_gate(workspace_id, True), transaction.atomic():
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        authority.assert_current(ledger)
        session = (
            ParticipantSession.objects.select_for_update()
            .filter(
                pk=session_id,
                version__study__workspace_id=workspace_id,
            )
            .first()
        )
        if session is None or type(revision) is not int or session.revision != revision:
            return None
        clock = session.activity_at
        if session.status == "submitted":
            clock = (
                Response.objects.filter(session=session)
                .values_list("committed_at", flat=True)
                .first()
            )
        if not clock or clock < session.started_at or now < expiry_cutoff(clock):
            return None
        return _accepted(
            ledger,
            "expiry",
            session.pk,
            session.version.study_id,
            [(session.pk, session.version_id)],
            "retention",
            journal,
        )


def _dispatch(command, fact, custody):
    case = command.case
    subjects = fact["sessions"]
    actual = (
        ParticipantSession.objects.select_for_update(of=("self",))
        .filter(pk__in=subjects)
        .values_list(
            "pk", "version_id", "version__study_id", "version__study__workspace_id"
        )
    )
    for subject, version, study, workspace in actual:
        if (
            workspace != case.workspace_id
            or study != case.study_id
            or str(version) != fact["versions"][str(subject)]
        ):
            raise Problem("ERASURE_SCOPE_MISMATCH", 409)
    whole = case.kind == "study"
    if command.owner == "U4":
        from researchloop.evidence_delivery.erasure import erase_scoped_delivery

        return erase_scoped_delivery(
            case.workspace_id,
            case.study_id,
            subjects,
            whole_study=whole,
            custody=custody,
        )
    from researchloop.analysis.erasure import erase_scoped_cohorts
    from researchloop.participation.erasure import erase_sessions

    methods = METHODS[command.owner]
    versions = PublishedVersion.objects.filter(
        study_id=case.study_id,
        study__workspace_id=case.workspace_id,
        study__method__in=methods,
    ).values_list("pk", flat=True)
    response_ids = set(
        map(
            str,
            Response.objects.filter(
                version_id__in=versions, session_id__in=subjects
            ).values_list("pk", flat=True),
        )
    )
    erase_scoped_cohorts(
        case.workspace_id, versions, subjects, response_ids, whole_study=whole
    )
    erase_sessions(case.workspace_id, case.study_id, methods, subjects)


def _claim(workspace_id, epoch):
    now = timezone.now()
    with transaction.atomic():
        command = (
            ErasureCommand.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(
                case__workspace_id=workspace_id,
                epoch=epoch,
            )
            .filter(
                Q(status="pending")
                | Q(status="retrying", due_at__lte=now)
                | Q(status="leased", lease_until__lte=now)
            )
            .order_by("id")
            .first()
        )
        if command is None:
            return None
        if command.attempt >= MAX_ATTEMPTS:
            command.status = "failed"
            command.save(update_fields=["status"])
            _update_case(command)
            return None
        command.attempt += 1
        command.lease_until = now + timedelta(seconds=15)
        command.status = "leased"
        command.save(update_fields=["attempt", "lease_until", "status"])
        return ErasureAttempt.objects.create(command=command, sequence=command.attempt)


def _update_case(command):
    case = ErasureCase.objects.select_for_update().get(pk=command.case_id)
    statuses = dict(
        case.commands.filter(epoch=command.epoch).values_list("owner", "status")
    )
    if statuses == dict.fromkeys(OWNERS, "completed") and (
        case.kind != "study" or case.lifecycle_closed
    ):
        if case.kind == "study":
            from researchloop.studies.erasure import erase_definition

            erase_definition(case.workspace_id, case.study_id)
        case.status = "completed"
    elif "failed" in statuses.values():
        case.status = "incomplete"
    elif "retrying" in statuses.values():
        case.status = "retrying"
    else:
        case.status = "accepted"
    case.save(update_fields=["status"])


def _finish(attempt, command, ok):
    if (
        command.pk != attempt.command_id
        or command.attempt != attempt.sequence
        or command.status != "leased"
    ):
        return False
    attempt = (
        ErasureAttempt.objects.select_for_update()
        .filter(
            pk=attempt.pk,
            command_id=command.pk,
            sequence=command.attempt,
            fence=attempt.fence,
            status="leased",
            acknowledged_at__isnull=True,
        )
        .first()
    )
    if attempt is None:
        return False
    acknowledged_at = timezone.now()
    if ok and (command.lease_until is None or acknowledged_at >= command.lease_until):
        return False
    attempt.status = "completed" if ok else "retryable"
    attempt.acknowledged_at = acknowledged_at
    attempt.save(update_fields=["status", "acknowledged_at"])
    command.status = (
        "completed"
        if ok
        else "failed"
        if command.attempt >= MAX_ATTEMPTS
        else "retrying"
    )
    command.due_at = (
        None
        if ok
        else acknowledged_at
        + timedelta(seconds=2**command.attempt, milliseconds=attempt.fence.int % 1000)
    )
    command.save(update_fields=["status", "due_at"])
    _update_case(command)
    if ok:
        record_audit("erasure-owner", "erasure-owner-completed", command.pk)
    return ok


def _permitted_journal(workspace_id, recovery_epoch):
    with delivery_gate(workspace_id, timeout_ms=500), transaction.atomic():
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        if recovery_epoch is None:
            authority.assert_current(ledger)
        elif not (
            journal.restore_state == "pending"
            and ledger.u5_quarantined
            and recovery_epoch == journal.epoch
            and (ledger.u5_head, ledger.u5_epoch) == (journal.head, journal.epoch)
        ):
            raise authority.Unavailable()
        return journal


def _replay_unprojected_append(workspace_id):
    with delivery_gate(workspace_id, True, timeout_ms=500), transaction.atomic():
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        if ledger.u5_head == journal.head:
            authority.assert_current(ledger)
        else:
            _reconcile_successor(ledger, journal)
        return journal


def run_one(workspace_id, *, recovery_epoch=None):
    """Claim briefly, then run bounded owner I/O outside the delivery gate."""
    with maintenance_gate(workspace_id, exclusive=False), ExitStack() as custody:
        try:
            journal = _permitted_journal(workspace_id, recovery_epoch)
        except authority.Unavailable:
            if recovery_epoch is not None:
                raise
            journal = _replay_unprojected_append(workspace_id)
        attempt = _claim(workspace_id, journal.epoch)
        if attempt is None:
            return False
        with transaction.atomic():
            command = ErasureCommand.objects.select_for_update().get(
                pk=attempt.command_id
            )
            if command.attempt != attempt.sequence or command.status != "leased":
                return False
            fact = next(
                item for item in journal.entries if item["id"] == str(command.case_id)
            )
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '500ms'")
                        cursor.execute("SET LOCAL statement_timeout = '750ms'")
                    _dispatch(command, fact, custody)
                    if not _finish(attempt, command, True):
                        # An unacknowledged delivery cannot commit owner writes.
                        raise Problem("ERASURE_ACKNOWLEDGEMENT_REJECTED", 503)
            except (Problem, OSError, DatabaseError):
                command.refresh_from_db()
                return _finish(attempt, command, False)
            return True
