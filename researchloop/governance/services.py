from researchloop.governance.models import AuditFact
from django.db import transaction
from researchloop.governance.models import Ledger, Suppression
from researchloop.governance.gate import delivery_gate
from researchloop.interaction.errors import Problem
from researchloop.governance.authority import assert_current


def record_audit(actor, action, reference):
    return AuditFact.objects.create(
        actor=str(actor), action=action, reference=str(reference)
    )


def check_suppression(workspace_id):
    ledger, _ = Ledger.objects.get_or_create(workspace_id=workspace_id)
    assert_current(ledger)
    if not ledger.available:
        raise Problem("GOVERNANCE_UNAVAILABLE", 503)
    return ledger.revision, set(
        Suppression.objects.filter(ledger=ledger).values_list("subject", flat=True)
    )


def read_delivery_fence(workspace_id):
    """Read-only generation check while caller retains the shared workspace gate.

    Suppression and barrier writers require the exclusive workspace gate.
    A long export capture must not keep the ledger row locked against live
    U2/U3 job authority reads in the same workspace.
    """
    ledger = Ledger.objects.filter(workspace_id=workspace_id).first()
    if ledger is None or not ledger.available:
        raise Problem("GOVERNANCE_UNAVAILABLE", 503)
    assert_current(ledger)
    if ledger.analysis_barrier:
        raise Problem("ANALYSIS_ERASURE_PENDING", 503)
    suppressed = set(
        Suppression.objects.filter(ledger=ledger).values_list("subject", flat=True)
    )
    return ledger.analysis_generation, ledger.revision, suppressed


def activate_suppression(workspace_id, subject):
    with delivery_gate(workspace_id, True), transaction.atomic():
        ledger, _ = Ledger.objects.get_or_create(workspace_id=workspace_id)
        ledger = Ledger.objects.select_for_update().get(pk=ledger.pk)
        if not ledger.available:
            raise Problem("GOVERNANCE_UNAVAILABLE", 503)
        _, created = Suppression.objects.get_or_create(
            ledger=ledger, subject=str(subject)
        )
        if created:
            ledger.revision += 1
            ledger.analysis_generation += 1
            ledger.save(update_fields=["revision", "analysis_generation"])
        return ledger.revision


def analysis_guard(
    workspace_id,
    *,
    expected_generation=None,
    allow_barrier=False,
    require_barrier=False,
):
    """Governance-owned generation; caller holds gate then transaction before owners."""
    ledger, _ = Ledger.objects.get_or_create(workspace_id=workspace_id)
    ledger = Ledger.objects.select_for_update().get(pk=ledger.pk)
    assert_current(ledger)
    if not ledger.available:
        raise Problem("GOVERNANCE_UNAVAILABLE", 503)
    if (
        expected_generation is not None
        and ledger.analysis_generation != expected_generation
    ):
        raise Problem("ANALYSIS_FENCED", 409)
    if ledger.analysis_barrier and not allow_barrier:
        raise Problem("ANALYSIS_ERASURE_PENDING", 503)
    if require_barrier and not ledger.analysis_barrier:
        raise Problem("ANALYSIS_BARRIER_REQUIRED", 409)
    return ledger.analysis_generation, ledger.revision


def begin_analysis_cleanup(workspace_id, subject):
    """Fence derived Analysis work only; not a full C07 erasure coordinator."""
    with delivery_gate(workspace_id, True, timeout_ms=500), transaction.atomic():
        ledger, _ = Ledger.objects.get_or_create(workspace_id=workspace_id)
        ledger = Ledger.objects.select_for_update().get(pk=ledger.pk)
        if not ledger.available:
            raise Problem("GOVERNANCE_UNAVAILABLE", 503)
        _, created = Suppression.objects.get_or_create(
            ledger=ledger, subject=str(subject)
        )
        ledger.revision += int(created)
        ledger.analysis_generation += 1
        ledger.analysis_barrier = True
        ledger.save(
            update_fields=["revision", "analysis_generation", "analysis_barrier"]
        )
        return ledger.analysis_generation


def assert_analysis_cleanup_subject(
    workspace_id, subject, generation, require_barrier=False
):
    analysis_guard(
        workspace_id,
        expected_generation=generation,
        allow_barrier=True,
        require_barrier=require_barrier,
    )
    if not Suppression.objects.filter(
        ledger__workspace_id=workspace_id, subject=str(subject)
    ).exists():
        raise Problem("ANALYSIS_SUPPRESSION_REQUIRED", 409)


def finish_analysis_cleanup(workspace_id, generation, component_acknowledgement):
    if (
        component_acknowledgement.get("component") != "Analysis"
        or component_acknowledgement.get("cleaned") is not True
        or component_acknowledgement.get("generation") != generation
        or component_acknowledgement.get("workspaceId") != workspace_id
    ):
        raise Problem("ANALYSIS_CLEANUP_INCOMPLETE", 409)
    with delivery_gate(workspace_id, True, timeout_ms=500), transaction.atomic():
        analysis_guard(workspace_id, expected_generation=generation, allow_barrier=True)
        Ledger.objects.filter(workspace_id=workspace_id).update(analysis_barrier=False)
        return {
            "analysisBarrierReleased": True,
            "generation": generation,
            "fullErasureComplete": False,
        }
