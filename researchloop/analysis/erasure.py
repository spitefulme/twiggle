"""Bounded derived-cache cleanup; never claims raw/all-owner C07 completion."""

import hashlib
import time

from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone

from researchloop.analysis.models import (
    AnalysisCohort,
    AnalysisJob,
    AnalysisRequestReceipt,
    AnalysisResult,
    AnnotationEvent,
    AnnotationState,
    DerivedCleanupReceipt,
)
from researchloop.governance.gate import delivery_gate
from researchloop.governance.services import (
    assert_analysis_cleanup_subject,
    record_audit,
)
from researchloop.interaction.errors import Problem


def erase_scoped_cohorts(
    workspace_id, versions, subjects, response_ids, *, whole_study
):
    """Remove only the eligible method's affected derived objects."""
    scope = AnalysisCohort.objects.filter(
        workspace_id=workspace_id, version_id__in=versions
    )
    events = AnnotationEvent.objects.filter(
        workspace_id=workspace_id, version_id__in=versions
    )
    states = AnnotationState.objects.filter(
        workspace_id=workspace_id, version_id__in=versions
    )
    if not whole_study:
        affected = Q(pk__in=[])
        for subject in subjects:
            affected |= Q(subject_references__contains=[subject])
            affected |= Q(snapshot__records__contains=[{"subjectReference": subject}])
        scope = scope.filter(affected)
        events = events.filter(
            Q(subject_reference__in=subjects) | Q(response_id__in=response_ids)
        )
        for state in states.select_for_update():
            coding = {
                key: value
                for key, value in state.coding.items()
                if key not in response_ids
            }
            exclusions = {
                key: value
                for key, value in state.exclusions.items()
                if key not in response_ids
            }
            if (coding, exclusions) != (state.coding, state.exclusions):
                state.coding, state.exclusions = coding, exclusions
                state.revision += 1
                state.save(update_fields=["coding", "exclusions", "revision"])
    else:
        states.delete()
    jobs = AnalysisJob.objects.filter(cohort__in=scope)
    AnalysisRequestReceipt.objects.filter(job__in=jobs).delete()
    AnalysisResult.objects.filter(cohort__in=scope).delete()
    jobs.delete()
    scope.delete()
    events.delete()


def erase_derived_subject(workspace_id, subject, generation):
    if not isinstance(subject, str) or not subject or type(generation) is not int:
        raise Problem("INVALID_ERASURE_COMMAND")
    started = time.monotonic()
    digest = hashlib.sha256(subject.encode()).hexdigest()
    with delivery_gate(workspace_id, True, timeout_ms=500), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '500ms'")
            cursor.execute("SET LOCAL statement_timeout = '750ms'")
        assert_analysis_cleanup_subject(workspace_id, subject, generation)
        receipt, _ = DerivedCleanupReceipt.objects.get_or_create(
            workspace_id=workspace_id, generation=generation, subject_digest=digest
        )
        receipt = DerivedCleanupReceipt.objects.select_for_update().get(pk=receipt.pk)
        if receipt.completed:
            return receipt.acknowledgement
        assert_analysis_cleanup_subject(
            workspace_id, subject, generation, require_barrier=True
        )
        if AnnotationEvent.objects.filter(
            workspace_id=workspace_id, subject_reference=""
        ).exists():
            raise Problem("ANALYSIS_ERASURE_RECONCILIATION_REQUIRED", 503)
        current = AnalysisCohort.objects.filter(
            workspace_id=workspace_id, subject_references__contains=[subject]
        )
        legacy = (
            AnalysisCohort.objects.filter(
                workspace_id=workspace_id, subject_references=[]
            )
            .filter(
                Q(snapshot__snapshotVersion__isnull=True)
                | ~Q(snapshot__snapshotVersion=2)
            )
            .filter(snapshot__records__contains=[{"subjectReference": subject}])
        )
        ids = list(current.order_by("pk").values_list("pk", flat=True)[:4])
        ids += list(
            legacy.exclude(pk__in=ids)
            .order_by("pk")
            .values_list("pk", flat=True)[: max(0, 4 - len(ids))]
        )
        cohorts = list(AnalysisCohort.objects.select_for_update().filter(pk__in=ids))
        events = list(
            AnnotationEvent.objects.filter(
                workspace_id=workspace_id, subject_reference=subject
            )
            .order_by("pk")
            .values("pk", "version_id", "response_id")[:256]
        )
        responses = {str(event["response_id"]) for event in events}
        versions = {event["version_id"] for event in events}
        for cohort in cohorts:
            versions.add(cohort.version_id)
            for row in cohort.snapshot["records"]:
                if row["subjectReference"] == subject and row["responseId"]:
                    responses.add(row["responseId"])
        for state in AnnotationState.objects.select_for_update().filter(
            workspace_id=workspace_id, version_id__in=versions
        ):
            changed = False
            for response_id in responses:
                changed |= state.coding.pop(response_id, None) is not None
                changed |= state.exclusions.pop(response_id, None) is not None
            if changed:
                state.revision += 1
                state.save(update_fields=["coding", "exclusions", "revision"])
        job_ids = list(
            AnalysisJob.objects.filter(cohort_id__in=ids).values_list("pk", flat=True)
        )
        counts = {
            "requestReceipts": AnalysisRequestReceipt.objects.filter(
                job_id__in=job_ids
            ).delete()[0],
            "results": AnalysisResult.objects.filter(cohort_id__in=ids).delete()[0],
            "jobs": AnalysisJob.objects.filter(pk__in=job_ids).delete()[0],
            "cohorts": AnalysisCohort.objects.filter(pk__in=ids).delete()[0],
            "annotationEvents": AnnotationEvent.objects.filter(
                pk__in=[event["pk"] for event in events]
            ).delete()[0],
        }
        remaining = (
            current.exists()
            or legacy.exists()
            or AnnotationEvent.objects.filter(
                workspace_id=workspace_id, subject_reference=subject
            ).exists()
        )
        if time.monotonic() - started > 1.5:
            raise Problem("ANALYSIS_CLEANUP_RETRY", 503)
        receipt.counts = {
            key: receipt.counts.get(key, 0) + value for key, value in counts.items()
        }
        receipt.completed = not remaining
        receipt.acknowledgement = {
            "component": "Analysis",
            "workspaceId": workspace_id,
            "generation": generation,
            "cleaned": not remaining,
            "retryable": bool(remaining),
            "counts": receipt.counts,
            "occurredAt": timezone.now().isoformat(),
            "fullErasureComplete": False,
        }
        receipt.save(update_fields=["counts", "completed", "acknowledgement"])
        record_audit(
            "analysis-owner",
            "analysis-derived-cleanup",
            str(workspace_id) + ":" + str(generation),
        )
        return receipt.acknowledgement
