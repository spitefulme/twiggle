"""Additive C01 Analysis routes; same-origin auth/CSRF remains in common API."""

import json

from django.http import JsonResponse

from researchloop.analysis import jobs, ports, services
from researchloop.interaction.errors import Problem


def handles(path):
    parts = path.split("/")
    return (
        len(parts) >= 3
        and parts[0] == "studies"
        and parts[2]
        in {
            "results",
            "analysis-jobs",
            "analysis-annotations",
            "analysis-evidence",
            "analysis-pairs",
        }
    )


def response(request, path):
    from researchloop.interaction.views import gated_response

    parts = path.split("/")
    if request.method == "POST":
        key = request.headers.get("Idempotency-Key", "")
        if not 16 <= len(key) <= 128 or len(request.body) > 262144:
            raise Problem("INVALID_REQUEST", 422)
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict) or not isinstance(data.get("versionId"), str):
            raise Problem("INVALID_REQUEST", 422)
        version_id = data["versionId"]
    elif request.method == "GET":
        data, key = {}, None
        version_id = request.GET.get("versionId")
        if not version_id:
            raise Problem("INVALID_REQUEST", 422)
    else:
        raise Problem("METHOD_NOT_ALLOWED", 405)
    public = ports.authorized_version(request.user, parts[1], version_id)
    selection_requested = (
        request.method == "GET"
        and len(parts) == 3
        and parts[2] == "results"
        and (
            "status" in request.GET
            or request.headers.get("Evidence-Selection") == "request"
        )
    )
    if public.method != "card-sort" and not (
        selection_requested and public.method == "tree-test-v2"
    ):
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    args = (request.user, parts[1], version_id)

    def render(payload, status=200, pending=False):
        rendered = JsonResponse(
            payload,
            status=status,
            json_dumps_params={"ensure_ascii": False, "separators": (",", ":")},
        )
        if pending:
            rendered["Location"] = payload["statusLocation"]
            rendered["Retry-After"] = "1"
        return rendered

    def produce():
        if parts[2] == "analysis-pairs" and len(parts) == 3 and request.method == "GET":
            return render(
                services.inspect_pair(
                    *args,
                    request.GET.get("cohortId"),
                    request.GET.get("leftCardId"),
                    request.GET.get("rightCardId"),
                )
            )
        if parts[2] == "analysis-jobs":
            if len(parts) == 3 and request.method == "POST":
                if set(data) != {"versionId", "metric"}:
                    raise Problem("INVALID_ANALYSIS_INPUT")
                return render(
                    jobs.request(*args, key, metric=data["metric"]), 202, True
                )
            if len(parts) == 4 and request.method == "GET":
                return render(jobs.status(*args, parts[3]))
            if len(parts) == 5 and parts[4] == "cancel" and request.method == "POST":
                if set(data) != {"versionId"}:
                    raise Problem("INVALID_REQUEST", 422)
                return render(jobs.cancel(*args, parts[3], key))
        if (
            parts[2] == "analysis-annotations"
            and len(parts) == 3
            and request.method == "POST"
        ):
            revision = request.headers.get("If-Match", "")
            if not revision.isdigit():
                raise Problem("INVALID_REQUEST", 422)
            return render(
                services.annotate(
                    *args,
                    int(revision),
                    {key: value for key, value in data.items() if key != "versionId"},
                    key,
                )
            )
        if parts[2] == "analysis-evidence" and request.method == "GET":
            if len(parts) == 3:
                return render(services.evidence_index(*args))
            if len(parts) == 4:
                return render(
                    services.inspect_response(
                        *args, parts[3], cohort_id=request.GET.get("cohortId")
                    )
                )
        if parts[2] == "results" and len(parts) == 3 and request.method == "GET":
            job_id = request.GET.get("jobId") or jobs.latest(*args)
            state = jobs.status(*args, job_id)
            if state["state"] in {"queued", "running", "stalled"}:
                return render(state, 202, True)
            if state["state"] != "succeeded":
                raise Problem("ANALYSIS_FAILED", 503)
            return render(jobs.result(*args, job_id))
        raise Problem("NOT_FOUND", 404)

    if selection_requested:
        from researchloop.evidence_delivery.selection import (
            filtered_response,
            status_filter,
        )

        status_filter(request.GET)
        job_id = request.GET.get("jobId") or jobs.latest(
            *args, metric="tree-v2" if public.method == "tree-test-v2" else "immediate"
        )
        state = jobs.status(*args, job_id)
        if state["state"] in {"queued", "running", "stalled"}:
            return render(state, 202, True)
        if state["state"] != "succeeded":
            raise Problem("ANALYSIS_FAILED", 503)
        return filtered_response(request, parts[1], version_id, job_id)
    return gated_response(public.workspace_id, produce)
