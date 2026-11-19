"""Versioned, same-origin boundary for the U3 tree method."""

from django.http import JsonResponse
import json

from researchloop.interaction.errors import Problem
from researchloop.participation import services as participants
from researchloop.studies import services as studies
from researchloop.trees.services import validate_v2


def author_draft(request, study_id):
    return studies.draft_data(require_tree(studies.get_study(request.user, study_id)))


def require_tree(study):
    if study.method != "tree-test-v2":
        raise Problem("NOT_FOUND", 404)
    return study


def preview(request, study_id):
    draft = author_draft(request, study_id)
    validate_v2(draft["content"], publishing=True)
    return JsonResponse(
        {
            "method": draft["method"],
            "draftId": draft["draftId"],
            "revision": draft["revision"],
            "content": draft["content"],
            "previewOnly": True,
        }
    )


def participant(request, token, tail):
    from researchloop.interaction.views import gated_response, mutation

    if tail == [] and request.method == "GET":
        session = participants.resolve(token)
        return gated_response(
            session.version.study.workspace_id,
            lambda: participants.resume_tree_v2(token),
        )
    if tail == ["events"] and request.method == "POST":
        session = participants.resolve(token)
        return gated_response(
            session.version.study.workspace_id,
            lambda: mutation(request, f"participant/{token}/tree-v2/events"),
        )
    raise Problem("NOT_FOUND", 404)


def results(request, study_id, version_id, action):
    from researchloop.analysis import jobs, ports
    from researchloop.interaction.views import gated_response

    args = request.user, study_id, version_id
    public = ports.authorized_version(*args)
    if public.method != "tree-test-v2":
        raise Problem("NOT_FOUND", 404)

    def produce():
        if action == "analysis-jobs" and request.method == "POST":
            key = request.headers.get("Idempotency-Key", "")
            if (
                not 16 <= len(key) <= 128
                or len(request.body) > 262144
                or json.loads(request.body or b"{}") != {}
            ):
                raise Problem("INVALID_REQUEST")
            acknowledgement = jobs.request(*args, key, metric="tree-v2")
            response = JsonResponse(acknowledgement, status=202)
            response["Location"] = acknowledgement["statusLocation"]
            response["Retry-After"] = "1"
            return response
        if request.method != "GET":
            raise Problem("METHOD_NOT_ALLOWED", 405)
        job_id = request.GET.get("jobId")
        if action == "analysis-jobs":
            if not job_id:
                raise Problem("INVALID_REQUEST")
            return jobs.status(*args, job_id)
        if action != "results":
            raise Problem("NOT_FOUND", 404)
        job_id = job_id or jobs.latest(*args, metric="tree-v2")
        status = jobs.status(*args, job_id)
        if status["state"] in {"queued", "running", "stalled"}:
            return JsonResponse(status, status=202)
        report = jobs.result(*args, job_id)
        attempts = report["attempts"]
        offset = int(request.GET.get("attemptOffset", "0"))
        if not 0 <= offset <= len(attempts):
            raise Problem("INVALID_REQUEST")
        return {
            **report,
            "attempts": attempts[offset : offset + 50],
            "nextAttemptOffset": offset + 50 if offset + 50 < len(attempts) else None,
        }

    return gated_response(public.workspace_id, produce)
