import json
from contextlib import ExitStack

from django.http import JsonResponse

from researchloop.evidence_delivery import candidates, downloads, handoff, jobs
from researchloop.identity.services import authorize
from researchloop.interaction.errors import Problem


def handles(parts):
    return parts[0] in {"exports", "downloads", "candidate-ias"} or (
        len(parts) == 3 and parts[0] == "studies" and parts[2] == "exports"
    )


def response(request, parts):
    if parts[0] == "downloads":
        if len(parts) == 2 and request.method == "GET":
            return downloads.response(parts[1])
        raise Problem("DOWNLOAD_FORBIDDEN", 403)
    authorize(request.user)
    with ExitStack() as resources:
        return handoff.protect(_owned_response(request, parts, resources), resources)


def _owned_response(request, parts, resources):
    if request.method == "GET":
        if len(parts) == 2 and parts[0] == "exports":
            return JsonResponse(
                jobs.status(request.user, parts[1], resources=resources)
            )
        if len(parts) == 2 and parts[0] == "candidate-ias":
            return JsonResponse(
                candidates.access(request.user, parts[1], resources=resources)
            )
        raise Problem("NOT_FOUND", 404)
    key = request.headers.get("Idempotency-Key", "")
    if not 16 <= len(key) <= 128 or len(request.body) > 262144:
        raise Problem("INVALID_REQUEST", 422)
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise Problem("INVALID_REQUEST", 422)
    if request.method == "POST" and parts[0] == "studies":
        return JsonResponse(
            jobs.admit(
                request.user,
                parts[1],
                data,
                request.headers.get("Evidence-Selection"),
                key,
                resources=resources,
            ),
            status=202,
        )
    if parts == ["candidate-ias"] and request.method == "POST":
        return JsonResponse(
            candidates.create(request.user, data, key, resources=resources), status=201
        )
    if len(parts) == 2 and parts[0] == "candidate-ias" and request.method == "PATCH":
        revision = request.headers.get("If-Match", "")
        if not revision.isascii() or not revision.isdigit() or len(revision) > 12:
            raise Problem("INVALID_REQUEST", 422)
        return JsonResponse(
            candidates.access(
                request.user,
                parts[1],
                data=data,
                expected=int(revision),
                key=key,
                resources=resources,
            )
        )
    raise Problem("NOT_FOUND", 404)
