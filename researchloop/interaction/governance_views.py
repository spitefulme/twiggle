import uuid

from django.db.models import CharField, Q, Subquery
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from researchloop.governance import authority
from researchloop.governance.coordinator import admit_study
from researchloop.governance.models import AuditFact, ErasureCase, ErasureCommand
from researchloop.identity.models import Membership
from researchloop.identity.services import authorize
from researchloop.interaction.errors import Problem
from researchloop.studies.models import (
    PreviewSnapshot,
    PublishedVersion,
    Study,
    StudyDraft,
)


@ensure_csrf_cookie
def shell(request):
    from researchloop.interaction.views import problem

    try:
        member = authorize(request.user, administrator=True)
    except Problem as error:
        return problem(error)
    return render(
        request,
        "u5/governance.html",
        {
            "workspace_id": member.workspace_id,
            "erasure_available": authority.configured(),
        },
    )


def _summary(case):
    return {"caseId": str(case.pk), "kind": case.kind, "status": case.status}


AUDIT_LABELS = {
    "membership-granted": "Grant member access",
    "membership-revoked": "Revoke member access",
    "study-created": "Create study",
    "draft-saved": "Save draft",
    "preview-created": "Create preview",
    "version-published": "Publish version",
    "closed": "Close study",
    "revoked": "Revoke study access",
    "erasure-admitted": "Accept erasure request",
    "erasure-owner-completed": "Complete owner erasure",
}


def _scoped_references(queryset, *actions):
    references = queryset.annotate(reference_id=Cast("pk", CharField())).values(
        "reference_id"
    )
    return Q(action__in=actions, reference__in=Subquery(references))


def _audit(workspace_id, owned_cases):
    members = Membership.objects.filter(workspace_id=workspace_id)
    study_scope = {"study__workspace_id": workspace_id}
    references = (
        _scoped_references(members, "membership-granted", "membership-revoked")
        | _scoped_references(
            Study.objects.filter(workspace_id=workspace_id),
            "study-created",
            "closed",
            "revoked",
        )
        | _scoped_references(StudyDraft.objects.filter(**study_scope), "draft-saved")
        | _scoped_references(
            PublishedVersion.objects.filter(**study_scope), "version-published"
        )
        | _scoped_references(
            PreviewSnapshot.objects.filter(draft__study__workspace_id=workspace_id),
            "preview-created",
        )
        | _scoped_references(owned_cases, "erasure-admitted")
        | _scoped_references(
            ErasureCommand.objects.filter(case__in=owned_cases),
            "erasure-owner-completed",
        )
    )
    # Legacy global denials and restore facts have no scope-proven object
    # reference. Appearance in this list must never be inferred from actor alone.
    facts = list(
        AuditFact.objects.filter(references).order_by("-occurred_at", "-pk")[:50]
    )
    candidate_ids = {
        int(row.actor)
        for row in facts
        if row.actor.isascii() and row.actor.isdigit() and len(row.actor) <= 18
    }
    accounts = {
        str(user_id): name
        for user_id, name in members.filter(user_id__in=candidate_ids).values_list(
            "user_id", "user__username"
        )
    }
    system = {"retention": "Retention worker", "erasure-owner": "Erasure worker"}
    return [
        {
            "actor": accounts.get(
                event.actor, system.get(event.actor, "Former workspace account")
            ),
            "action": event.action,
            "actionLabel": AUDIT_LABELS[event.action],
            "occurredAt": event.occurred_at.isoformat(),
        }
        for event in facts
    ]


def case_response(request, workspace_id, tail, data=None):
    authorize(request.user, workspace_id, administrator=True)
    owned_cases = ErasureCase.objects.filter(workspace_id=workspace_id)
    if request.method == "GET" and not tail:
        return JsonResponse(
            {
                "cases": [_summary(case) for case in owned_cases.order_by("-pk")[:100]],
                "audit": _audit(workspace_id, owned_cases),
            }
        )
    if request.method == "GET" and len(tail) == 1:
        item = owned_cases.filter(pk=uuid.UUID(tail[0])).first()
        if item:
            return JsonResponse(_summary(item))
    if request.method == "POST" and not tail:
        if (
            set(data) != {"studyId", "revision", "confirmation"}
            or data["studyId"] != data["confirmation"]
        ):
            raise Problem("CONFIRMATION_REQUIRED", 422)
        item = admit_study(
            request.user,
            workspace_id,
            uuid.UUID(data["studyId"]),
            revision=data["revision"],
        )
        return JsonResponse(_summary(item), status=202)
    raise Problem("NOT_FOUND", 404)
