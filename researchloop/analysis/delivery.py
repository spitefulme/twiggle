"""Owner-only adapter attaching exact contributor identities to frozen projections."""

from dataclasses import dataclass

from researchloop.analysis import ports
from researchloop.analysis.models import AnalysisJob, AnalysisResult, AnnotationState
from researchloop.governance.services import read_delivery_fence
from researchloop.interaction.errors import Problem


@dataclass
class Evidence:
    public: object
    result: object
    rows: list
    contributors: set
    generation: int

    def selected(self, status):
        return [row for row in self.rows if status is None or status in row["statuses"]]


def _card_rows(public, cohort, suppressed):
    snapshot = cohort.snapshot
    captured = snapshot.get("records")
    if not isinstance(captured, list):
        raise Problem("COHORT_STALE", 409)
    current = {
        row["evidenceId"]: row
        for row in ports.analysis_snapshot(
            public.version_id, public.content, suppressed
        )["records"]
    }
    included = set(cohort.included_response_ids)
    rows, contributors = [], set()
    for fact in captured:
        row = current.get(fact["evidenceId"])
        if row is None or any(
            row.get(key) != fact.get(key)
            for key in ("subjectReference", "stateRevision", "rawDigest", "status")
        ):
            raise Problem("COHORT_STALE", 409)
        contributors.add(row["subjectReference"])
        tags = {"started", row["status"]}
        if row["structuralStatus"] == "malformed":
            tags.add("invalid")
        if row["responseId"] in included:
            tags.add("included")
        if row["responseId"] in cohort.exclusion_reasons:
            tags.add("excluded")
        rows.append(
            {
                "evidenceId": row["evidenceId"],
                "responseId": row["responseId"],
                "status": row["status"],
                "structuralStatus": row["structuralStatus"],
                "statuses": sorted(tags),
                "groups": [
                    {
                        key: group[key]
                        for key in ("groupId", "label", "parentGroupId")
                        if key in group and isinstance(group[key], (str, type(None)))
                    }
                    for group in row["groups"]
                    if isinstance(group, dict)
                ],
                "membership": {
                    key: value
                    for key, value in row["membership"].items()
                    if isinstance(value, str)
                },
            }
        )
    return rows, contributors


def _tree_rows(public, cohort, suppressed, payload):
    attempts, attribution, _ = ports.tree_analysis_snapshot(
        public.version_id, suppressed, attribution=True
    )
    current = {attempt["attemptId"]: (attempt, events) for attempt, events in attempts}
    captured = {
        row["attemptId"]: (row, events) for row, events in cohort.snapshot["attempts"]
    }
    included = set(cohort.included_response_ids)
    if not captured.keys() <= current.keys() or any(
        current[key] != val for key, val in captured.items()
    ):
        raise Problem("COHORT_STALE", 409)
    if not captured.keys() <= attribution.keys():
        raise Problem("COHORT_STALE", 409)
    # New participants on the same published version are not contributors to
    # this older, immutable cohort. Resolve only owner-attributed captured IDs.
    subjects = {attribution[key] for key in captured}
    if subjects != set(cohort.subject_references):
        raise Problem("COHORT_STALE", 409)
    rows = []
    visible = {row["attemptId"]: row for row in payload["attempts"]}
    for identity, (attempt, _) in captured.items():
        tags = {"started"} if attempt["startedAt"] else set()
        if attempt["terminalAt"]:
            tags.add("submitted")
        elif tags:
            tags.add("partial")
        if attempt.get("gap"):
            tags.add("invalid")
        if identity in included:
            tags.add("included")
        if identity in cohort.exclusion_reasons:
            tags.add("excluded")
        rows.append(
            {
                **visible.get(
                    identity, {"attemptId": identity, "taskId": attempt["taskId"]}
                ),
                "statuses": sorted(tags),
            }
        )
    return rows, set(subjects)


def from_result(
    user, study_id, version_id, *, result_id=None, job_id=None, cohort_id=None
):
    """Use inside a Governance delivery gate and transaction owned by caller."""
    public = ports.authorized_version(user, study_id, version_id)
    generation, suppression_revision, suppressed = read_delivery_fence(
        public.workspace_id
    )
    queryset = AnalysisResult.objects.select_related("cohort").filter(
        cohort__workspace_id=public.workspace_id,
        cohort__study_id=public.study_id,
        cohort__version_id=public.version_id,
    )
    if job_id:
        bound = AnalysisJob.objects.filter(
            pk=job_id,
            version_id=public.version_id,
            workspace_id=public.workspace_id,
            status="succeeded",
            deletion_generation=generation,
        ).values("cohort_id")[:1]
        queryset = queryset.filter(cohort_id__in=bound)
    elif result_id:
        queryset = queryset.filter(pk=result_id)
    elif cohort_id:
        queryset = queryset.filter(cohort_id=cohort_id)
    else:
        raise Problem("COHORT_STALE", 409)
    matches = list(queryset[:2])
    if len(matches) != 1:
        raise Problem("COHORT_STALE", 409)
    result = matches[0]
    cohort = result.cohort
    complete_subjects = set(cohort.subject_references)
    if complete_subjects & suppressed:
        raise Problem("SUPPRESSED", 403)
    if (
        cohort.suppression_revision != suppression_revision
        or not AnalysisJob.objects.filter(
            cohort=cohort, status="succeeded", deletion_generation=generation
        ).exists()
    ):
        raise Problem("COHORT_STALE", 409)
    if public.method == "tree-test-v2":
        rows, subjects = _tree_rows(public, cohort, suppressed, result.payload)
    elif public.method == "card-sort":
        annotation = AnnotationState.objects.filter(
            version_id=public.version_id
        ).first()
        if annotation is None or annotation.revision != cohort.annotation_revision:
            raise Problem("COHORT_STALE", 409)
        rows, subjects = _card_rows(public, cohort, suppressed)
    else:
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    if subjects != complete_subjects:
        raise Problem("COHORT_STALE", 409)
    return Evidence(public, result, rows, subjects, generation)


def source_study(version_id, result_id):
    study_id = (
        AnalysisResult.objects.filter(pk=result_id, cohort__version_id=version_id)
        .values_list("cohort__study_id", flat=True)
        .first()
    )
    if study_id is None:
        raise Problem("NOT_FOUND", 404)
    return str(study_id)


def authorize_stamp(user, selected):
    """Reauthorize an already captured exact binding without hydrating evidence."""
    public = ports.authorized_version(
        user, str(selected.study_id), str(selected.version_id)
    )
    generation, revision, suppressed = read_delivery_fence(public.workspace_id)
    if set(selected.contributor_ids) & suppressed:
        raise Problem("SUPPRESSED", 403)
    if (
        selected.revoked
        or public.workspace_id != selected.workspace_id
        or generation != selected.generation
    ):
        raise Problem("COHORT_STALE", 409)
    result = (
        AnalysisResult.objects.filter(
            pk=selected.analysis_id,
            cohort_id=selected.cohort_id,
            cohort__workspace_id=public.workspace_id,
            cohort__study_id=public.study_id,
            cohort__version_id=public.version_id,
            cohort__suppression_revision=revision,
        )
        .values("cohort__subject_references", "cohort__annotation_revision")
        .first()
    )
    if (
        result is None
        or set(result["cohort__subject_references"]) != set(selected.contributor_ids)
        or not AnalysisJob.objects.filter(
            cohort_id=selected.cohort_id,
            status="succeeded",
            deletion_generation=generation,
        ).exists()
    ):
        raise Problem("COHORT_STALE", 409)
    if public.method == "card-sort":
        annotation = (
            AnnotationState.objects.filter(version_id=public.version_id)
            .values_list("revision", flat=True)
            .first()
        )
        if annotation != result["cohort__annotation_revision"]:
            raise Problem("COHORT_STALE", 409)
