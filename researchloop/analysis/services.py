from django.db import transaction, DatabaseError
import copy
import hashlib
import json
import uuid
from collections import Counter

from researchloop.governance.services import check_suppression
from researchloop.governance.services import analysis_guard
from researchloop.governance.services import record_audit
from researchloop.governance.gate import delivery_gate
from researchloop.identity.services import authorize
from researchloop.interaction.errors import Problem
from researchloop.participation.evidence import minimal_snapshot
from researchloop.studies.models import PublishedVersion
from researchloop.studies.services import get_study
from researchloop.analysis import ports
from researchloop.analysis.trees import score_tree_v2 as score_tree_v2
from researchloop.analysis.cards import (
    coded_view,
    compute_matrices,
    contract_projection,
    select_cohort,
)
from researchloop.analysis.models import (
    AnnotationState,
    AnnotationEvent,
    AnalysisCohort,
    AnalysisJob,
    AnalysisResult,
)
from researchloop.cards.labels import validate_label


def erase_derived_subject(workspace_id, subject, generation):
    from researchloop.analysis.erasure import erase_derived_subject as cleanup

    return cleanup(workspace_id, subject, generation)


def card_evidence_projection(data):
    """C04 internal owner port; never appends bulk raw evidence to C01 HTTP.

    Subject reference selects an authoritative captured/live owner identity,
    never a caller-provided suppression decision. Return remains inside the
    Governance delivery gate; U4 must separately guard its eventual delivery.
    """
    from researchloop.identity.services import resolve_authorized_actor

    required = {"researcherId", "versionId", "cohortId", "subjectReference"}
    if (
        not isinstance(data, dict)
        or set(data) != required
        or any(not isinstance(value, str) for value in data.values())
    ):
        raise Problem("INVALID_ANALYSIS_INPUT")
    if not data["researcherId"].isdigit():
        raise Problem("UNAUTHORIZED", 401)
    try:
        for field in ["versionId", "cohortId", "subjectReference"]:
            uuid.UUID(data[field])
    except ValueError as error:
        raise Problem("VERSION_MISMATCH", 409) from error
    cohort = (
        AnalysisCohort.objects.filter(pk=data["cohortId"], version_id=data["versionId"])
        .only("study_id", "workspace_id")
        .first()
    )
    if cohort is None:
        raise Problem("VERSION_MISMATCH", 409)
    try:
        user = resolve_authorized_actor(data["researcherId"], cohort.workspace_id)
    except Problem as error:
        raise Problem("UNAUTHORIZED", 401) from error

    def project(version, evidence, state, suppression_revision):
        generation, _ = analysis_guard(version.workspace_id)
        captured = AnalysisCohort.objects.get(
            pk=data["cohortId"],
            version_id=version.version_id,
            workspace_id=version.workspace_id,
        )
        if (
            captured.suppression_revision != suppression_revision
            or captured.annotation_revision != state.revision
        ):
            raise Problem("COHORT_STALE", 409)
        if data["subjectReference"] not in captured.subject_references:
            raise Problem("UNAUTHORIZED", 401)
        current = {row["evidenceId"]: row for row in evidence["records"]}
        if not any(
            row["subjectReference"] == data["subjectReference"]
            for row in current.values()
        ):
            raise Problem("SUPPRESSED", 403)
        finished = AnalysisJob.objects.filter(
            cohort=captured, status="succeeded", deletion_generation=generation
        ).exists()
        stored = AnalysisResult.objects.filter(cohort=captured).first()
        if not finished or stored is None:
            raise Problem("COHORT_STALE", 409)
        responses = []
        for fact in captured.snapshot["records"]:
            row = current.get(fact["evidenceId"])
            if (
                row is None
                or row["stateRevision"] != fact["stateRevision"]
                or row["rawDigest"] != fact["rawDigest"]
            ):
                raise Problem("COHORT_STALE", 409)
            # Incomplete sessions have no terminal responseId. Their captured
            # counts remain explicit; C01's evidence index exposes working state.
            if row["responseId"] is None:
                continue
            labels = {
                group["groupId"]: group["label"]
                for group in row["groups"]
                if isinstance(group, dict)
                and isinstance(group.get("groupId"), str)
                and isinstance(group.get("label"), str)
            }
            nested = []
            if row["structuralStatus"] != "malformed":
                groups = {group["groupId"]: group for group in row["groups"]}
                for card in captured.snapshot["cards"]:
                    path, parent = [], row["membership"].get(card["cardId"])
                    while parent is not None:
                        path.insert(0, parent)
                        parent = groups[parent].get("parentGroupId")
                    nested.append({"cardId": card["cardId"], "groupPath": path})
            responses.append(
                {
                    "responseId": row["responseId"],
                    "nestedMembership": nested,
                    "originalLabels": labels,
                    "structuralStatus": row["structuralStatus"],
                }
            )
        # Fresh authoritative check at assembly/return, not a cached permit.
        final_revision, final_suppressed = check_suppression(version.workspace_id)
        analysis_guard(version.workspace_id, expected_generation=generation)
        if data["subjectReference"] in final_suppressed:
            raise Problem("SUPPRESSED", 403)
        if final_revision != suppression_revision:
            raise Problem("COHORT_STALE", 409)
        record_audit(user.pk, "card-evidence-projection", str(captured.pk))
        return {
            "versionId": str(version.version_id),
            "cohortManifest": copy.deepcopy(stored.payload["cohort"]),
            "responseCounts": copy.deepcopy(captured.counts),
            "responses": responses,
            "analysis": {
                "immediateSimilarity": copy.deepcopy(
                    stored.payload["immediateSimilarity"]
                ),
                "ancestorSimilarity": copy.deepcopy(
                    stored.payload["ancestorSimilarity"]
                ),
                "clusteringManifest": copy.deepcopy(stored.payload["dendrogram"]),
            },
        }

    try:
        return _owned_context(user, str(cohort.study_id), data["versionId"], project)
    except Problem as error:
        if error.code in {"NOT_FOUND", "FORBIDDEN"}:
            raise Problem("UNAUTHORIZED", 401) from error
        if error.code in {"ANALYSIS_ERASURE_PENDING", "ANALYSIS_FENCED"}:
            raise Problem("COHORT_STALE", 409) from error
        raise
    except DatabaseError as error:
        raise Problem("GOVERNANCE_UNAVAILABLE", 503) from error


def _owned_context(user, study_id, version_id, operation):
    public = ports.authorized_version(user, study_id, version_id)
    if public.method != "card-sort":
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    with delivery_gate(public.workspace_id), transaction.atomic():
        analysis_guard(public.workspace_id)
        suppression_revision, suppressed = check_suppression(public.workspace_id)
        version = ports.authorized_version(user, study_id, version_id, lock=True)
        evidence = ports.analysis_snapshot(
            version.version_id, version.content, suppressed
        )
        state, _ = AnnotationState.objects.get_or_create(
            version_id=version.version_id,
            defaults={"workspace_id": version.workspace_id},
        )
        state = AnnotationState.objects.select_for_update().get(pk=state.pk)
        return operation(version, evidence, state, suppression_revision)


def capture(user, study_id, version_id, metric="immediate"):
    if metric not in {"immediate", "ancestor"}:
        raise Problem("INVALID_ANALYSIS_INPUT")

    def freeze(version, evidence, state, suppression_revision):
        # Suppressed annotations never enter a new cohort, even if owner cleanup
        # is pending; the original history is not rewritten by this read.
        response_ids = {
            row["responseId"] for row in evidence["records"] if row["responseId"]
        }
        exclusions = {
            key: value for key, value in state.exclusions.items() if key in response_ids
        }
        coding = {
            key: value for key, value in state.coding.items() if key in response_ids
        }
        # Selection uses identity/status metadata only. Do not deep-copy every
        # lossless raw sort merely to obtain the selected IDs/counts. The exact
        # validated owner records below still supply all immutable input facts.
        selected = select_cohort(
            [
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"rawState", "groups", "membership"}
                }
                for row in evidence["records"]
            ],
            exclusions,
        )
        facts = []
        for row in evidence["records"]:
            record = {
                key: row[key]
                for key in [
                    "responseId",
                    "evidenceId",
                    "subjectReference",
                    "status",
                    "structuralStatus",
                    "preview",
                    "stateRevision",
                    "rawDigest",
                ]
            }
            eligible = (
                row["status"] == "submitted" and row["structuralStatus"] == "valid"
            )
            record["groups"] = (
                [
                    {
                        "groupId": group["groupId"],
                        "parentGroupId": group.get("parentGroupId"),
                    }
                    for group in row["groups"]
                ]
                if eligible
                else []
            )
            record["membership"] = copy.deepcopy(row["membership"]) if eligible else {}
            facts.append(record)
        snapshot = {
            "snapshotVersion": 2,
            "records": facts,
            "cards": version.content["cards"],
            "coding": coding,
            "exclusions": exclusions,
            "metric": metric,
            "populationExplanation": selected["populationExplanation"],
            "previewPolicy": "Live-session population excludes the separate preview store.",
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "snapshot": snapshot,
                    "evidenceRevision": evidence["evidenceRevision"],
                    "annotationRevision": state.revision,
                    "suppressionRevision": suppression_revision,
                    "frozenRevisionId": str(version.frozen_revision_id),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cohort, _ = AnalysisCohort.objects.get_or_create(
            version_id=version.version_id,
            fingerprint=fingerprint,
            defaults={
                "workspace_id": version.workspace_id,
                "study_id": version.study_id,
                "frozen_revision_id": version.frozen_revision_id,
                "evidence_revision": evidence["evidenceRevision"],
                "annotation_revision": state.revision,
                "suppression_revision": suppression_revision,
                "included_response_ids": [
                    row["responseId"] for row in selected["included"]
                ],
                "exclusion_reasons": selected["exclusionReasons"],
                "counts": selected["counts"],
                "snapshot": snapshot,
                "subject_references": [
                    row["subjectReference"] for row in evidence["records"]
                ],
            },
        )
        return cohort

    return _owned_context(user, study_id, version_id, freeze)


def annotate(user, study_id, version_id, expected_revision, data, key):
    if (
        not isinstance(data, dict)
        or data.get("kind") not in {"coding", "exclusion"}
        or type(data.get("active")) is not bool
    ):
        raise Problem("INVALID_ANNOTATION")
    required = {"kind", "responseId", "active"} | (
        {"groupId", "label"} if data["kind"] == "coding" else {"reason"}
    )
    if (
        set(data) != required
        or type(expected_revision) is not int
        or expected_revision < 0
        or not isinstance(key, str)
        or not 1 <= len(key) <= 128
    ):
        raise Problem("INVALID_ANNOTATION")
    if not isinstance(data["responseId"], str) or (
        data["kind"] == "coding" and not isinstance(data["groupId"], str)
    ):
        raise Problem("INVALID_ANNOTATION")
    validate_label(
        data["label"] if data["kind"] == "coding" else data["reason"], participant=False
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {"revision": expected_revision, "data": data},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def amend(version, evidence, state, suppression_revision):
        target = next(
            (
                row
                for row in evidence["records"]
                if row["responseId"] == data["responseId"]
            ),
            None,
        )
        if target is None:
            raise Problem("NOT_FOUND", 404)
        if data["kind"] == "coding" and not any(
            isinstance(group, dict) and group.get("groupId") == data["groupId"]
            for group in target["groups"]
        ):
            raise Problem("NOT_FOUND", 404)
        prior = AnnotationEvent.objects.filter(
            version_id=version.version_id, actor_id=str(user.pk), key=key
        ).first()
        if prior:
            if prior.digest != fingerprint:
                raise Problem("IDEMPOTENCY_CONFLICT", 409)
            return prior.acknowledgement
        if state.revision != expected_revision:
            raise Problem("STALE_REVISION", 409, revision=state.revision)
        if data["kind"] == "coding":
            assignments = state.coding.setdefault(data["responseId"], {})
            if data["active"]:
                assignments[data["groupId"]] = data["label"]
            else:
                assignments.pop(data["groupId"], None)
                if not assignments:
                    state.coding.pop(data["responseId"], None)
        elif data["active"]:
            state.exclusions[data["responseId"]] = data["reason"]
        else:
            state.exclusions.pop(data["responseId"], None)
        state.revision += 1
        state.save(update_fields=["coding", "exclusions", "revision"])
        acknowledgement = {
            "annotationRevision": state.revision,
            "versionId": str(version.version_id),
            "saveState": "saved",
        }
        AnnotationEvent.objects.create(
            version_id=version.version_id,
            workspace_id=version.workspace_id,
            actor_id=str(user.pk),
            revision=state.revision,
            response_id=data["responseId"],
            subject_reference=target["subjectReference"],
            kind=data["kind"],
            group_id=data.get("groupId"),
            payload=copy.deepcopy(data),
            key=key,
            digest=fingerprint,
            acknowledgement=acknowledgement,
        )
        record_audit(
            user.pk,
            "analysis-" + data["kind"],
            str(version.version_id) + ":" + str(state.revision),
        )
        return acknowledgement

    return _owned_context(user, study_id, version_id, amend)


def calculate(cohort, check=lambda: None):
    if cohort.snapshot["metric"] == "tree-v2":
        from researchloop.analysis.tree_projection import calculate as calculate_tree

        return calculate_tree(cohort, check)
    included_ids = set(cohort.included_response_ids)
    rows = [
        row for row in cohort.snapshot["records"] if row["responseId"] in included_ids
    ]
    result = compute_matrices(
        [card["cardId"] for card in cohort.snapshot["cards"]],
        rows,
        check=check,
        metric=cohort.snapshot["metric"],
    )
    manifest = {
        "cohortId": str(cohort.pk),
        "versionId": str(cohort.version_id),
        "inclusionRules": [
            "valid",
            "submitted",
            "nonpreview",
            "unsuppressed",
            "not-researcher-excluded",
        ],
        "includedResponseIds": cohort.included_response_ids,
        "exclusionReasons": cohort.exclusion_reasons,
    }
    payload = contract_projection(
        result, manifest, cohort.counts, cohort.frozen_revision_id
    )
    payload.update(
        studyId=str(cohort.study_id),
        evidenceRevision=cohort.evidence_revision,
        annotationRevision=cohort.annotation_revision,
        populationExplanation=cohort.snapshot["populationExplanation"],
        previewPolicy=cohort.snapshot["previewPolicy"],
        cards=copy.deepcopy(cohort.snapshot["cards"]),
    )
    return payload


def inspect_response(user, study_id, version_id, evidence_id, *, cohort_id=None):
    def inspect(version, evidence, state, suppression_revision):
        row = next(
            (row for row in evidence["records"] if row["evidenceId"] == evidence_id),
            None,
        )
        if row is None:
            raise Problem("NOT_FOUND", 404)
        coding = state.coding
        annotation_revision = state.revision
        if cohort_id is not None:
            cohort = AnalysisCohort.objects.filter(
                pk=cohort_id,
                version_id=version.version_id,
                workspace_id=version.workspace_id,
            ).first()
            if cohort is None:
                raise Problem("NOT_FOUND", 404)
            captured = next(
                (
                    entry
                    for entry in cohort.snapshot["records"]
                    if entry["evidenceId"] == evidence_id
                ),
                None,
            )
            if captured is None:
                raise Problem("NOT_FOUND", 404)
            if (
                cohort.suppression_revision != suppression_revision
                or captured.get("rawDigest") != row["rawDigest"]
                or captured["stateRevision"] != row["stateRevision"]
            ):
                raise Problem("COHORT_STALE", 409)
            coding = cohort.snapshot["coding"]
            annotation_revision = cohort.annotation_revision
        if row["structuralStatus"] == "malformed":
            groups = copy.deepcopy(row["groups"])
        else:
            assignments = coding.get(row["responseId"], {})
            groups = coded_view([row], {row["responseId"]: assignments})[0]["groups"]
        record_audit(
            user.pk,
            "analysis-inspection",
            str(version.version_id) + ":" + str(evidence_id),
        )
        return {
            "evidenceId": evidence_id,
            "responseId": row["responseId"],
            "versionId": str(version.version_id),
            "frozenRevisionId": str(version.frozen_revision_id),
            "stateRevision": row["stateRevision"],
            "annotationRevision": annotation_revision,
            "structuralStatus": row["structuralStatus"],
            "status": row["status"],
            "rawState": copy.deepcopy(row["rawState"]),
            "groups": groups,
            "codingAvailable": row["structuralStatus"] != "malformed"
            and row["responseId"] is not None,
            "annotationHistory": [
                {
                    "revision": event.revision,
                    "kind": event.kind,
                    "groupId": event.group_id,
                    "active": event.payload["active"],
                    "label": event.payload.get("label"),
                    "reason": event.payload.get("reason"),
                    "occurredAt": event.occurred_at.isoformat(),
                }
                for event in AnnotationEvent.objects.filter(
                    version_id=version.version_id,
                    workspace_id=version.workspace_id,
                    response_id=row["responseId"],
                    revision__lte=annotation_revision,
                ).order_by("revision")
            ]
            if row["responseId"]
            else [],
        }

    return _owned_context(user, study_id, version_id, inspect)


def inspect_pair(user, study_id, version_id, cohort_id, left, right):
    def inspect(version, evidence, state, suppression_revision):
        cohort = AnalysisCohort.objects.filter(
            pk=cohort_id,
            version_id=version.version_id,
            workspace_id=version.workspace_id,
        ).first()
        if cohort is None:
            raise Problem("NOT_FOUND", 404)
        identifiers = {card["cardId"] for card in cohort.snapshot["cards"]}
        if left not in identifiers or right not in identifiers or left == right:
            raise Problem("INVALID_ANALYSIS_INPUT")
        if cohort.suppression_revision != suppression_revision:
            raise Problem("COHORT_STALE", 409)
        current = {row["responseId"]: row for row in evidence["records"]}
        captured = {row["responseId"]: row for row in cohort.snapshot["records"]}
        labels = Counter()
        for identifier in cohort.included_response_ids:
            row, frozen = current.get(identifier), captured[identifier]
            if (
                row is None
                or row["rawDigest"] != frozen["rawDigest"]
                or row["stateRevision"] != frozen["stateRevision"]
            ):
                raise Problem("COHORT_STALE", 409)
            groups = {group["groupId"]: group for group in row["groups"]}
            first, second = row["membership"].get(left), row["membership"].get(right)
            selected = first if first == second else None
            if cohort.snapshot["metric"] == "ancestor":

                def chain(group_id):
                    result = []
                    while group_id is not None:
                        result.append(group_id)
                        group_id = groups[group_id].get("parentGroupId")
                    return result

                right_chain = set(chain(second))
                selected = next(
                    (group for group in chain(first) if group in right_chain), None
                )
            if selected is not None:
                labels[groups[selected]["label"]] += 1
        record_audit(user.pk, "analysis-pair-inspection", str(cohort.pk))
        return {
            "versionId": str(version.version_id),
            "cohortId": str(cohort.pk),
            "metric": cohort.snapshot["metric"],
            "leftCardId": left,
            "rightCardId": right,
            "numerator": sum(labels.values()),
            "denominator": len(cohort.included_response_ids),
            "rawLabels": [
                {"label": label, "count": count}
                for label, count in sorted(
                    labels.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        }

    if not isinstance(cohort_id, str):
        raise Problem("INVALID_ANALYSIS_INPUT")
    return _owned_context(user, study_id, version_id, inspect)


def evidence_index(user, study_id, version_id):
    def index(version, evidence, state, suppression_revision):
        response_ids = {
            row["responseId"] for row in evidence["records"] if row["responseId"]
        }
        selected = select_cohort(
            evidence["records"],
            {
                key: reason
                for key, reason in state.exclusions.items()
                if key in response_ids
            },
        )
        included = {row["responseId"] for row in selected["included"]}
        return {
            "versionId": str(version.version_id),
            "frozenRevisionId": str(version.frozen_revision_id),
            "annotationRevision": state.revision,
            "evidenceRevision": evidence["evidenceRevision"],
            "counts": selected["counts"],
            "populationExplanation": selected["populationExplanation"],
            "rows": [
                {
                    "evidenceId": row["evidenceId"],
                    "responseId": row["responseId"],
                    "status": row["status"],
                    "structuralStatus": row["structuralStatus"],
                    "stateRevision": row["stateRevision"],
                    "included": row["responseId"] in included,
                    "exclusionReason": selected["exclusionReasons"].get(
                        row["responseId"]
                    ),
                }
                for row in evidence["records"]
            ],
        }

    return _owned_context(user, study_id, version_id, index)


@transaction.atomic
def minimal(user, study_id, version_id):
    study = get_study(user, study_id, True)
    authorize(user, study.workspace_id)
    version = (
        PublishedVersion.objects.select_for_update()
        .filter(pk=version_id, study=study)
        .first()
    )
    if version is None:
        raise Problem("NOT_FOUND", 404)
    _, suppressed = check_suppression(study.workspace_id)
    counts, evidence, revision = minimal_snapshot(version, suppressed)
    return {
        "projection": "minimal-v1",
        "studyId": str(study.pk),
        "versionId": str(version.pk),
        "frozenRevisionId": str(version.frozen_revision),
        "evidenceRevision": revision,
        "counts": counts,
        "completion": {
            "numerator": counts["submitted"],
            "denominator": counts["started"],
            "fraction": counts["submitted"] / counts["started"]
            if counts["started"]
            else None,
            "label": "submitted-started-session-rate",
        },
        "evidence": evidence,
        "analysisAvailability": "full-analysis-not-in-this-projection",
    }
