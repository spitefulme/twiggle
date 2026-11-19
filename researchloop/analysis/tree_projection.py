"""Tree cohort capture and authorized C05 result projection."""

import hashlib
import json
from collections import defaultdict

from django.db import transaction

from researchloop.analysis import ports
from researchloop.analysis.models import AnalysisCohort, AnalysisJob, AnalysisResult
from researchloop.analysis.trees import score_tree_v2
from researchloop.governance.gate import delivery_gate
from researchloop.governance.services import analysis_guard, check_suppression
from researchloop.interaction.errors import Problem


def capture(user, study_id, version_id):
    public = ports.authorized_version(user, study_id, version_id)
    if public.method != "tree-test-v2":
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    with delivery_gate(public.workspace_id), transaction.atomic():
        analysis_guard(public.workspace_id)
        suppression_revision, suppressed = check_suppression(public.workspace_id)
        public = ports.authorized_version(user, study_id, version_id)
        attempts, subjects, evidence_revision = ports.tree_analysis_snapshot(
            public.version_id, suppressed
        )
        snapshot = {
            "metric": "tree-v2",
            "definition": {
                "occurrences": public.content["occurrences"],
                "tasks": public.content["tasks"],
            },
            "attempts": attempts,
        }
        binding = [
            str(public.frozen_revision_id),
            evidence_revision,
            suppression_revision,
            snapshot,
        ]
        fingerprint = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        cohort, _ = AnalysisCohort.objects.get_or_create(
            version_id=public.version_id,
            fingerprint=fingerprint,
            defaults={
                "workspace_id": public.workspace_id,
                "study_id": public.study_id,
                "frozen_revision_id": public.frozen_revision_id,
                "evidence_revision": evidence_revision,
                "annotation_revision": 0,
                "suppression_revision": suppression_revision,
                "included_response_ids": [
                    row["attemptId"] for row, _ in attempts if row["startedAt"]
                ],
                "exclusion_reasons": {
                    row["attemptId"]: "never-started"
                    for row, _ in attempts
                    if not row["startedAt"]
                },
                "counts": {},
                "snapshot": snapshot,
                "subject_references": subjects,
            },
        )
        return cohort


def calculate(cohort, check):
    result = score_tree_v2(
        cohort.snapshot["definition"], cohort.snapshot["attempts"], check
    )
    grouped = defaultdict(list)
    for pair in cohort.snapshot["attempts"]:
        grouped[pair[0]["taskId"]].append(pair)
    by_task = []
    for task in cohort.snapshot["definition"]["tasks"]:
        check()
        tables = score_tree_v2(
            cohort.snapshot["definition"], grouped[task["taskId"]], check
        )
        tables.pop("attempts")
        by_task.append(
            {"taskId": task["taskId"], "prompt": task["prompt"], "tables": tables}
        )
    return {
        "versionId": str(cohort.version_id),
        "cohortManifest": {
            "cohortId": str(cohort.pk),
            "versionId": str(cohort.version_id),
            "includedAttemptIds": cohort.included_response_ids,
            "exclusionReasons": cohort.exclusion_reasons,
        },
        "exposureCountE": result["counts"]["E"],
        "terminalCountF": result["counts"]["F"],
        "attempts": result.pop("attempts"),
        "resultTables": result,
        "taskResults": by_task,
    }


def project(user, study_id, version_id, cohort_id, subject_reference):
    """C05 internal boundary; suppression and output scope are re-evaluated."""
    try:
        public = ports.authorized_version(user, study_id, version_id)
    except Problem as error:
        if error.code in {"NOT_FOUND", "FORBIDDEN"}:
            raise Problem("UNAUTHORIZED", 401) from error
        raise
    if public.method != "tree-test-v2":
        raise Problem("VERSION_MISMATCH", 409)
    with delivery_gate(public.workspace_id), transaction.atomic():
        current = ports.authorized_version(user, study_id, version_id)
        if (
            current.workspace_id != public.workspace_id
            or current.method != "tree-test-v2"
        ):
            raise Problem("UNAUTHORIZED", 401)
        revision, suppressed = check_suppression(public.workspace_id)
        if subject_reference in suppressed:
            raise Problem("SUPPRESSED", 423)
        generation, _ = analysis_guard(public.workspace_id)
        result = (
            AnalysisResult.objects.select_related("cohort")
            .filter(
                cohort_id=cohort_id,
                cohort__version_id=public.version_id,
                cohort__workspace_id=public.workspace_id,
                cohort__suppression_revision=revision,
                cohort__snapshot__metric="tree-v2",
            )
            .first()
        )
        if result is None or subject_reference not in result.cohort.subject_references:
            raise Problem("COHORT_STALE", 409)
        if not AnalysisJob.objects.filter(
            cohort_id=cohort_id,
            workspace_id=public.workspace_id,
            deletion_generation=generation,
            status="succeeded",
        ).exists():
            raise Problem("COHORT_STALE", 409)
        if any(subject in suppressed for subject in result.cohort.subject_references):
            raise Problem("SUPPRESSED", 423)
        return result.payload
