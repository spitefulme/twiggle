import hashlib
import json
from contextlib import ExitStack

from django.db import transaction

from researchloop.analysis import delivery, ports
from researchloop.evidence_delivery import handoff
from researchloop.evidence_delivery.models import Selection
from researchloop.interaction.errors import Problem


STATUSES = {"started", "partial", "submitted", "invalid", "excluded", "included"}


def status_filter(query):
    options = query.getlist("status")
    if len(options) > 1 or (options and options[0] not in STATUSES):
        raise Problem("INVALID_EVIDENCE_STATUS", 422)
    return options[0] if options else None


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def capture(user, evidence, status=None):
    data = {
        "actor_id": user.pk,
        "workspace_id": evidence.public.workspace_id,
        "study_id": str(evidence.public.study_id),
        "version_id": str(evidence.public.version_id),
        "analysis_id": str(evidence.result.pk),
        "cohort_id": str(evidence.result.cohort_id),
        "filter_status": status or "",
        "contributor_ids": sorted(evidence.contributors),
        "generation": evidence.generation,
    }
    return Selection.objects.get_or_create(
        fingerprint=fingerprint(
            [
                data,
                [
                    row.get("responseId")
                    or row.get("evidenceId")
                    or row.get("attemptId")
                    for row in evidence.selected(status)
                ],
            ]
        ),
        defaults=data,
    )[0]


def current(user, selected, *, hydrate=True):
    if selected.revoked or selected.actor_id != user.pk:
        raise Problem("COHORT_STALE", 409)
    if not hydrate:
        delivery.authorize_stamp(user, selected)
        return None
    proof = delivery.from_result(
        user,
        str(selected.study_id),
        str(selected.version_id),
        result_id=selected.analysis_id,
    )
    if (
        proof.generation != selected.generation
        or str(proof.result.cohort_id) != str(selected.cohort_id)
        or proof.contributors != set(selected.contributor_ids)
    ):
        raise Problem("COHORT_STALE", 409)
    return proof


def filtered_response(request, study_id, version_id, job_id):
    status = status_filter(request.GET)
    public = ports.authorized_version(request.user, study_id, version_id)
    with ExitStack() as resources:
        response = _presentation(
            request, study_id, version_id, job_id, status, public, resources
        )
        return handoff.protect(response, resources)


def _presentation(request, study_id, version_id, job_id, status, public, resources):
    with handoff.scope(public.workspace_id, resources), transaction.atomic():
        proof = delivery.from_result(request.user, study_id, version_id, job_id=job_id)
        selected = capture(request.user, proof, status)
        details = proof.selected(status)
        bounds = request.GET.getlist("detailOffset")
        if len(bounds) > 1 or (
            bounds
            and (
                not bounds[0].isascii() or not bounds[0].isdigit() or len(bounds[0]) > 6
            )
        ):
            raise Problem("INVALID_REQUEST", 422)
        offset = int(bounds[0]) if bounds else 0
        if not 0 <= offset <= len(details):
            raise Problem("INVALID_REQUEST", 422)
        from django.http import JsonResponse

        payload = dict(proof.result.payload)
        page = details[offset : offset + 25]
        next_offset = offset + 25 if offset + 25 < len(details) else None
        if public.method == "tree-test-v2":
            # The immutable analysis/export still retains every attempt. The
            # opt-in selection response must not also duplicate the full raw
            # attempt array, bypassing the bounded detail handoff.
            payload["attempts"] = page
            payload["nextAttemptOffset"] = next_offset
        response = JsonResponse(
            {
                **payload,
                "cohortManifest": payload.get("cohortManifest") or payload["cohort"],
                "evidenceSelection": {
                    "analysisId": str(proof.result.pk),
                    "status": status,
                    "detailCount": len(details),
                    "details": page,
                    "nextOffset": next_offset,
                },
            },
            json_dumps_params={"ensure_ascii": False, "separators": (",", ":")},
        )
        response["Evidence-Selection"] = str(selected.pk)
        return response
