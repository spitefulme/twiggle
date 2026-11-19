"""Trusted synthetic restore proof; never imports or overwrites a backup."""

import hmac
from uuid import uuid4

from django.db import transaction
from django.db.models import F

from researchloop.analysis.models import AnalysisCohort, AnalysisJob, AnalysisResult
from researchloop.evidence_delivery.models import (
    Candidate,
    DownloadGrant,
    Export,
    Selection,
)
from researchloop.governance import authority
from researchloop.governance.coordinator import OWNERS, ledger_for_update, project
from researchloop.governance.gate import delivery_gate, maintenance_gate
from researchloop.governance.models import ErasureCommand
from researchloop.governance.services import record_audit
from researchloop.interaction.errors import Problem
from researchloop.participation.models import ParticipantSession, Response
from researchloop.studies.models import PublishedVersion, Study


def initialize(workspace_id):
    """Explicit trusted-only genesis for a new isolated synthetic workspace."""
    with delivery_gate(workspace_id, True), transaction.atomic():
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id, initialize=True)
        if journal.head or ledger.u5_head or ledger.u5_epoch or ledger.u5_quarantined:
            raise authority.Unavailable()
        project(ledger, authority.publish(workspace_id, 0, ()))


def _row(kind, identifier, study, version="", *, subjects=(), parents=()):
    return [
        kind,
        str(identifier),
        str(study),
        str(version),
        sorted(map(str, subjects)),
        list(map(str, parents)),
    ]


def _owned_definitions(workspace_id):
    rows = [
        _row("study", pk, pk)
        for pk in Study.objects.filter(workspace_id=workspace_id).values_list(
            "pk", flat=True
        )
    ]
    rows.extend(
        _row("version", pk, study, pk)
        for pk, study in PublishedVersion.objects.filter(
            study__workspace_id=workspace_id
        ).values_list("pk", "study_id")
    )
    return rows


def _participants(workspace_id):
    rows = [
        _row("session", pk, study, version, subjects=[pk])
        for pk, study, version in ParticipantSession.objects.filter(
            version__study__workspace_id=workspace_id
        ).values_list("pk", "version__study_id", "version_id")
    ]
    rows.extend(
        _row("response", pk, study, version, subjects=[session], parents=[session])
        for pk, study, version, session in Response.objects.filter(
            version__study__workspace_id=workspace_id
        ).values_list("pk", "version__study_id", "version_id", "session_id")
    )
    return rows


def _analysis(workspace_id):
    cohorts = AnalysisCohort.objects.filter(workspace_id=workspace_id)
    fields = (
        "cohort__study_id",
        "cohort__version_id",
        "cohort__subject_references",
        "cohort_id",
    )
    rows = [
        _row("cohort", pk, study, version, subjects=subjects)
        for pk, study, version, subjects in cohorts.values_list(
            "pk", "study_id", "version_id", "subject_references"
        )
    ]
    for kind, model in (("result", AnalysisResult), ("job", AnalysisJob)):
        rows.extend(
            _row(kind, pk, study, version, subjects=subjects, parents=[cohort])
            for pk, study, version, subjects, cohort in model.objects.filter(
                cohort__in=cohorts
            ).values_list("pk", *fields)
        )
    return rows


def _delivery(workspace_id):
    scope = Selection.objects.filter(workspace_id=workspace_id)
    rows = [
        _row(
            "selection", pk, study, version, subjects=subjects, parents=[cohort, result]
        )
        for pk, study, version, subjects, cohort, result in scope.values_list(
            "pk",
            "study_id",
            "version_id",
            "contributor_ids",
            "cohort_id",
            "analysis_id",
        )
    ]
    fields = (
        "selection__study_id",
        "selection__version_id",
        "selection__contributor_ids",
        "selection_id",
    )
    for kind, model in (("candidate", Candidate), ("export", Export)):
        rows.extend(
            _row(kind, pk, study, version, subjects=subjects, parents=[selected])
            for pk, study, version, subjects, selected in model.objects.filter(
                selection__in=scope
            ).values_list("pk", *fields)
        )
    rows.extend(
        _row("grant", digest, study, version, subjects=subjects, parents=[job])
        for digest, study, version, subjects, job in DownloadGrant.objects.filter(
            job__selection__in=scope
        ).values_list(
            "digest",
            "job__selection__study_id",
            "job__selection__version_id",
            "job__selection__contributor_ids",
            "job_id",
        )
    )
    return rows


def _inventory(workspace_id):
    return sorted(
        _owned_definitions(workspace_id)
        + _participants(workspace_id)
        + _analysis(workspace_id)
        + _delivery(workspace_id)
    )


def capture_manifest(workspace_id):
    """Seal known-good identity evidence before taking the synthetic snapshot."""
    with delivery_gate(workspace_id, True), transaction.atomic():
        ledger = ledger_for_update(workspace_id)
        authority.assert_current(ledger)
        journal = authority.read(workspace_id)
        value = {
            "snapshot": str(uuid4()),
            "workspace": workspace_id,
            "head": journal.head,
            "epoch": journal.epoch,
            "prefix": len(journal.entries),
            "sourceManifest": journal.manifest,
            "sourceRestoreState": journal.restore_state,
            "sourceCompletion": journal.completion,
            "inventory": _inventory(workspace_id),
        }
        return {"manifest": value, "seal": _seal(value)}


def _seal(value):
    return authority.seal({"purpose": "synthetic-restore-manifest", "value": value})


def _verified(workspace_id, frame, journal):
    try:
        value, seal = frame["manifest"], frame["seal"]
        prefix = value["prefix"]
        if (
            not hmac.compare_digest(_seal(value), seal)
            or value["workspace"] != workspace_id
        ):
            raise ValueError
        if type(prefix) is not int or not 0 <= prefix <= len(journal.entries):
            raise ValueError
        source = authority.journal_value(
            workspace_id,
            value["epoch"],
            journal.entries[:prefix],
            value["sourceManifest"],
            restore_state=value["sourceRestoreState"],
            completion=value["sourceCompletion"],
        )
        if not hmac.compare_digest(authority.seal(source), value["head"]):
            raise ValueError
        return value, seal
    except (KeyError, ValueError, TypeError) as error:
        raise authority.Unavailable() from error


def start_restore(workspace_id, frame):
    """Compare trusted pre-cleanup scope; a restart retains its generation."""
    with (
        maintenance_gate(workspace_id, exclusive=True),
        delivery_gate(workspace_id, True),
        transaction.atomic(),
    ):
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        _, seal = _verified(workspace_id, frame, journal)
        if ledger.u5_epoch > journal.epoch:
            raise authority.Unavailable()
        if journal.restore_state == "pending":
            if journal.manifest != seal:
                raise authority.Unavailable()
        else:
            journal = authority.publish(
                workspace_id,
                journal.epoch + 1,
                journal.entries,
                seal,
                restore_state="pending",
            )
        project(ledger, journal, quarantined=True)
        return journal.epoch


def _expected(inventory, entries):
    studies = {fact["study"] for fact in entries if fact["kind"] == "study"}
    subjects = {subject for fact in entries for subject in fact["sessions"]}
    return [
        row
        for row in inventory
        if row[0] in {"study", "version"}
        or (row[2] not in studies and not subjects.intersection(row[4]))
    ]


def _pending_head(workspace_id, journal):
    return authority.seal(
        authority.journal_value(
            workspace_id,
            journal.epoch,
            journal.entries,
            journal.manifest,
            restore_state="pending",
        )
    )


def _completion_proof(workspace_id, journal, manifest):
    commands = ErasureCommand.objects.filter(
        case__workspace_id=workspace_id,
        epoch=journal.epoch,
        status="completed",
        attempts__sequence=F("attempt"),
        attempts__status="completed",
        attempts__acknowledged_at__isnull=False,
    ).order_by("case_id", "owner")
    evidence = [
        [str(case), owner, str(command), attempt, str(fence), stamp.isoformat()]
        for case, owner, command, attempt, fence, stamp in commands.values_list(
            "case_id",
            "owner",
            "pk",
            "attempt",
            "attempts__fence",
            "attempts__acknowledged_at",
        )
    ]
    required = {(item["id"], owner) for item in journal.entries for owner in OWNERS}
    inventory = _inventory(workspace_id)
    if (
        len(evidence) != len(required)
        or {(row[0], row[1]) for row in evidence} != required
        or inventory != _expected(manifest["inventory"], journal.entries)
    ):
        raise Problem("RECOVERY_RECONCILIATION_PENDING", 409)
    proof = authority.seal(
        {
            "purpose": "synthetic-restore-completion",
            "workspace": workspace_id,
            "epoch": journal.epoch,
            "manifest": journal.manifest,
            "receipts": evidence,
            "inventory": inventory,
        }
    )
    return proof, len(inventory)


def finish_restore(workspace_id, frame, epoch):
    with (
        maintenance_gate(workspace_id, exclusive=True),
        delivery_gate(workspace_id, True),
        transaction.atomic(),
    ):
        ledger = ledger_for_update(workspace_id)
        journal = authority.read(workspace_id)
        value, seal = _verified(workspace_id, frame, journal)
        if (
            (epoch, seal) != (journal.epoch, journal.manifest)
            or journal.restore_state not in {"pending", "completed"}
            or (ledger.u5_epoch, ledger.u5_head)
            != (epoch, _pending_head(workspace_id, journal))
            or not ledger.u5_quarantined
        ):
            raise authority.Unavailable()
        proof, identity_count = _completion_proof(workspace_id, journal, value)
        if journal.restore_state == "completed":
            # The canonical commit survived. The exact pending local
            # projection can finish only with the same verified proof.
            if not hmac.compare_digest(journal.completion, proof):
                raise Problem("RECOVERY_RECONCILIATION_PENDING", 409)
        else:
            journal = authority.publish(
                workspace_id,
                epoch,
                journal.entries,
                seal,
                restore_state="completed",
                completion=proof,
            )
        ledger.u5_quarantined = False
        ledger.u5_head = journal.head
        ledger.save(update_fields=["u5_quarantined", "u5_head"])
        record_audit("recovery", "restore-reconciled", value["snapshot"])
        return {
            "ready": True,
            "epoch": epoch,
            "reconciledIdentities": identity_count,
        }
