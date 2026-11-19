import hashlib
import json
import uuid

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.db import transaction, DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from researchloop.identity.services import authorize
from researchloop.interaction.errors import Problem
from researchloop.studies import services
from researchloop.studies.models import MutationReceipt, Study


def problem(error):
    body = {
        "type": "about:blank",
        "code": error.code,
        "title": error.code.replace("_", " ").capitalize(),
        "status": error.status,
        "requestId": str(uuid.uuid4()),
    }
    if error.revision is not None:
        body["currentRevision"] = error.revision
    if error.field_errors:
        body["fieldErrors"] = error.field_errors
    response = JsonResponse(
        body, status=error.status, content_type="application/problem+json"
    )
    if error.status == 429:
        response["Retry-After"] = "600"
    if error.status == 503 and error.code.startswith("ANALYSIS_"):
        response["Retry-After"] = "1"
    return response


def api(request, path=""):
    try:
        from researchloop.governance.readiness import assert_fresh_local

        assert_fresh_local()
        path = path.rstrip("/")
        if path == "studies" or path.startswith("studies/"):
            if not request.user.is_authenticated or not request.user.is_active:
                raise Problem("UNAUTHORIZED", 401)
        if (
            path.startswith("recruit/")
            and path.endswith("/sessions")
            and request.method == "POST"
        ):
            from researchloop.participation.services import throttle

            throttle(request.META.get("REMOTE_ADDR", "local"))
            request.u1_start_attempt_counted = True
        if path.endswith("/minimal-results") and request.method == "GET":
            return minimal_response(request, path)
        from researchloop.interaction import analysis_views

        if analysis_views.handles(path):
            return analysis_views.response(request, path)
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "workspaces" and parts[2] == "erasure-cases":
            from researchloop.interaction.governance_views import case_response

            if request.method == "GET":
                return case_response(request, int(parts[1]), parts[3:])
            if request.method != "POST":
                raise Problem("METHOD_NOT_ALLOWED", 405)
            key = request.headers.get("Idempotency-Key", "")
            if not 16 <= len(key) <= 128 or len(request.body) > 262144:
                raise Problem("INVALID_REQUEST", 422)
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise Problem("INVALID_REQUEST", 422)
            return case_response(request, int(parts[1]), parts[3:], data)
        from researchloop.interaction import evidence_views

        if evidence_views.handles(parts):
            return evidence_views.response(request, parts)
        from researchloop.governance.authority import configured

        if (
            configured()
            and len(parts) == 3
            and parts[0] == "participant"
            and parts[2] in {"operations", "submit"}
            and request.method == "POST"
        ):
            from researchloop.participation.services import resolve

            session = resolve(parts[1])
            return gated_response(
                session.version.study.workspace_id, lambda: mutation(request, path)
            )
        if len(parts) >= 3 and parts[0] == "participant" and parts[2] == "tree-v2":
            from researchloop.interaction.tree_views import participant

            return participant(request, parts[1], parts[3:])
        if (
            len(parts) == 3
            and parts[0] == "participant"
            and parts[2] == "submit"
            and request.method == "POST"
        ):
            from researchloop.participation.services import resolve

            session = resolve(parts[1])
            if session.version.study.method == "tree-test-v2":
                return gated_response(
                    session.version.study.workspace_id, lambda: mutation(request, path)
                )
        if len(parts) == 4 and parts[0] == "studies" and parts[2] == "tree-v2":
            from researchloop.interaction import tree_views

            if parts[3] == "draft" and request.method == "GET":
                return governed_study_response(
                    request,
                    path,
                    lambda: JsonResponse(tree_views.author_draft(request, parts[1])),
                )
            if parts[3] == "preview" and request.method == "GET":
                return governed_study_response(
                    request, path, lambda: tree_views.preview(request, parts[1])
                )
            if parts[3] != "draft" or request.method != "PATCH":
                raise Problem("NOT_FOUND", 404)
        if (
            len(parts) == 6
            and parts[0] == "studies"
            and parts[2] == "versions"
            and parts[4] == "tree-v2"
        ):
            from researchloop.interaction.tree_views import results

            return results(request, parts[1], parts[3], parts[5])
        if path.startswith("participant/") and request.method == "GET":
            from researchloop.participation.services import resolve, resume

            token = path.split("/")[1]
            session = resolve(token)
            return gated_response(
                session.version.study.workspace_id, lambda: resume(token)
            )
        if (
            path.startswith("recruit/")
            and path.endswith("/sessions")
            and request.method == "POST"
        ):
            study = Study.objects.filter(
                recruitment_digest=services.digest(path.split("/")[1])
            ).first()
            if study is not None:
                return gated_response(
                    study.workspace_id, lambda: mutation(request, path)
                )
        if request.method in {"POST", "PATCH", "DELETE"}:
            return governed_study_response(
                request, path, lambda: mutation(request, path)
            )
        if request.method != "GET":
            raise Problem("METHOD_NOT_ALLOWED", 405)
        return governed_study_response(
            request, path, lambda: JsonResponse(read(request, path), safe=False)
        )
    except Problem as error:
        if error.status in {401, 403, 404}:
            from researchloop.governance.services import record_audit

            try:
                record_audit(
                    request.user.pk if request.user.is_authenticated else "anonymous",
                    "access-denied",
                    error.code,
                )
            except DatabaseError:
                return problem(Problem("STORAGE_UNAVAILABLE", 503))
        return problem(error)
    except DatabaseError:
        return problem(Problem("STORAGE_UNAVAILABLE", 503))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, ValidationError):
        return problem(Problem("INVALID_REQUEST", 422))


def mutation(request, path):
    key = request.headers.get("Idempotency-Key", "")
    if not 16 <= len(key) <= 128 or len(request.body) > 262144:
        raise Problem("INVALID_REQUEST", 422)
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise Problem("INVALID_REQUEST", 422)
    parts = path.split("/")
    if parts[0] in {"participant", "recruit"}:
        from researchloop.participation import services as participation

        if (
            len(parts) == 4
            and parts[0] == "participant"
            and parts[2:] == ["tree-v2", "events"]
        ):
            return JsonResponse(participation.operate_tree_v2(parts[1], data, key))
        if len(parts) == 3 and parts[0] == "participant":
            function = {
                "operations": participation.operate,
                "submit": participation.finalize,
            }.get(parts[2])
            if function:
                return JsonResponse(function(parts[1], data, key))
        if len(parts) == 3 and parts[0] == "recruit" and parts[2] == "sessions":
            return JsonResponse(
                participation.start(
                    parts[1],
                    data,
                    request.META.get("REMOTE_ADDR", "local"),
                    key,
                    attempt_accounted=getattr(
                        request, "u1_start_attempt_counted", False
                    ),
                ),
                status=201,
            )
        raise Problem("NOT_FOUND", 404)
    # Authentication is checked on every replay, never inferred from a receipt.
    if path == "auth/session":
        payload, status = dispatch(request, path, data)
        return JsonResponse(payload, status=status)
    if path != "auth/session":
        if len(parts) >= 2 and parts[0] == "workspaces":
            from researchloop.identity.services import lock_administrator

            # Lock before checking receipts: a prior receipt never grants access.
            with transaction.atomic():
                lock_administrator(request.user, int(parts[1]))
                return authenticated_mutation(request, path, parts, data, key)
        authorize(request.user)
    if len(parts) >= 2 and parts[0] == "studies":
        services.get_study(request.user, parts[1])
    return authenticated_mutation(request, path, parts, data, key)


def authenticated_mutation(request, path, parts, data, key):
    actor = (
        str(request.user.pk)
        if request.user.is_authenticated
        else "anonymous:"
        + hashlib.sha256(request.session.session_key.encode()).hexdigest()
        if request.session.session_key
        else "anonymous"
    )
    fingerprint = hashlib.sha256(
        (request.method + path).encode()
        + request.body
        + request.headers.get("If-Match", "").encode()
    ).hexdigest()
    with transaction.atomic():
        with __import__(
            "django.db", fromlist=["connection"]
        ).connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", [actor + key]
            )
        receipt = MutationReceipt.objects.filter(actor=actor, key=key).first()
        if receipt:
            if receipt.digest != fingerprint:
                raise Problem("IDEMPOTENCY_CONFLICT", 409)
            payload = dict(receipt.payload)
            if "versionId" in payload and "versionNumber" in payload:
                payload["recruitmentLink"] = "/recruit/" + services.recruitment_token(
                    parts[1]
                )
            return JsonResponse(payload, status=receipt.status)
        payload, status = dispatch(request, path, data)
        stored = {k: v for k, v in payload.items() if k != "recruitmentLink"}
        MutationReceipt.objects.create(
            actor=actor, key=key, digest=fingerprint, payload=stored, status=status
        )
        return JsonResponse(payload, status=status)


def dispatch(request, path, data):
    if path == "auth/session":
        if request.method == "DELETE":
            logout(request)
            return {}, 200
        if set(data) != {"username", "password"}:
            raise Problem("INVALID_REQUEST")
        user = authenticate(
            request, username=data["username"], password=data["password"]
        )
        if user is None:
            raise Problem("UNAUTHORIZED", 401)
        member = authorize(user)
        login(request, user)
        return {"researcherId": str(user.pk), "role": member.role}, 200
    if path == "studies" and request.method == "POST":
        return services.create(request.user, data), 201
    parts = path.split("/")
    if (
        len(parts) == 3
        and parts[0] == "workspaces"
        and parts[2] == "members"
        and request.method == "POST"
    ):
        from researchloop.identity.services import admit_member

        return admit_member(request.user, int(parts[1]), data), 201
    if (
        len(parts) == 4
        and parts[0] == "workspaces"
        and parts[2] == "members"
        and request.method == "PATCH"
    ):
        from researchloop.identity.services import change_member

        return change_member(request.user, int(parts[1]), int(parts[3]), data), 200
    if len(parts) >= 2 and parts[0] == "studies":
        expected = int(request.headers.get("If-Match", "0"))
        if len(parts) == 2 and request.method == "PATCH":
            return services.save(request.user, parts[1], data, expected), 200
        if parts[2:] == ["tree-v2", "draft"] and request.method == "PATCH":
            from researchloop.interaction.tree_views import require_tree

            require_tree(services.get_study(request.user, parts[1]))
            return services.save(request.user, parts[1], data, expected), 200
        if parts[-1] == "preview":
            return services.preview(request.user, parts[1], data, expected), 201
        if parts[-1] == "versions":
            return services.publish(
                request.user,
                parts[1],
                data,
                expected,
                request.headers["Idempotency-Key"],
            ), 201
        if parts[-1] == "lifecycle":
            return services.lifecycle(request.user, parts[1], data), 200
    raise Problem("NOT_FOUND", 404)


def read(request, path):
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "members":
        from researchloop.identity.services import list_members

        return list_members(request.user, int(parts[1]))
    if len(parts) == 2 and parts[0] == "participant":
        from researchloop.participation.services import resume

        return resume(parts[1])
    if path == "auth/session/current":
        member = authorize(request.user)
        return {"researcherId": str(request.user.pk), "role": member.role}
    if path == "studies":
        member = authorize(request.user)
        return [
            services.draft_data(s)
            for s in Study.objects.filter(workspace_id=member.workspace_id).exclude(
                state="deleted"
            )
        ]
    parts = path.split("/")
    if len(parts) == 2 and parts[0] == "studies":
        return services.draft_data(services.get_study(request.user, parts[1]))
    if len(parts) == 3 and parts[0] == "studies" and parts[2] == "results":
        services.get_study(request.user, parts[1])
        raise Problem("FULL_ANALYSIS_UNAVAILABLE", 503)
    raise Problem("NOT_FOUND", 404)


def minimal_response(request, path):
    from researchloop.analysis.services import minimal

    study = services.get_study(request.user, path.split("/")[1])
    return gated_response(
        study.workspace_id,
        lambda: minimal(request.user, str(study.pk), request.GET.get("versionId")),
    )


def governed_study_response(request, path, produce):
    from researchloop.governance.authority import Unavailable, configured
    from researchloop.governance.models import Ledger
    from researchloop.governance.services import check_suppression

    if not (path == "studies" or path.startswith("studies/")):
        return produce()
    parts = path.split("/")
    workspace_id = (
        authorize(request.user).workspace_id
        if len(parts) == 1
        else services.get_study(request.user, parts[1]).workspace_id
    )
    if not configured():
        if (
            Ledger.objects.filter(workspace_id=workspace_id)
            .exclude(u5_head="", u5_epoch=0, u5_quarantined=False)
            .exists()
        ):
            raise Unavailable()
        return produce()

    def checked():
        check_suppression(workspace_id)
        return produce()

    return gated_response(workspace_id, checked)


def gated_response(workspace_id, producer):
    from researchloop.governance.gate import delivery_gate

    gate = delivery_gate(workspace_id)
    gate.__enter__()
    try:
        payload = producer()
        response = (
            payload if isinstance(payload, JsonResponse) else JsonResponse(payload)
        )
        if len(response.content) > 2097152:
            raise Problem("EVIDENCE_UNAVAILABLE", 503)
        response._resource_closers.append(lambda: gate.__exit__(None, None, None))
        return response
    except BaseException:
        gate.__exit__(None, None, None)
        raise


@ensure_csrf_cookie
def shell(request):
    return render(request, "u1/researcher.html")


@ensure_csrf_cookie
def participant_shell(request, capability):
    return render(request, "u1/participant.html")


def health(request, readiness=False):
    if not readiness:
        return JsonResponse({"alive": True})
    from researchloop.governance.readiness import assert_fresh_local
    from researchloop.evidence_delivery.transport import worker_ready

    try:
        report = {"ready": True, **assert_fresh_local()}
        if worker_ready():
            report["u4GuardedSyncWorker"] = True
        return JsonResponse(report)
    except (Problem, DatabaseError):
        return JsonResponse(
            {"ready": False, "operationalRecovery": "not-qualified"}, status=503
        )
