from researchloop.identity.models import Membership
from researchloop.interaction.errors import Problem


def lock_administrator(user, workspace_id):
    """Recheck administrator membership under the transaction's owner lock."""
    from researchloop.identity.models import Workspace

    authorize(user, workspace_id, administrator=True)
    Workspace.objects.select_for_update().get(pk=workspace_id)
    return authorize(user, workspace_id, administrator=True)


def list_members(user, workspace_id):
    authorize(user, workspace_id, administrator=True)
    return {
        "members": [
            {
                "userId": row.user_id,
                "username": row.user.get_username(),
                "role": row.role,
                "active": row.active,
                "revision": row.revision,
            }
            for row in Membership.objects.filter(workspace_id=workspace_id)
            .select_related("user")
            .order_by("pk")
        ]
    }


def admit_member(user, workspace_id, data):
    from django.contrib.auth import get_user_model
    from researchloop.governance.services import record_audit

    lock_administrator(user, workspace_id)
    username = data.get("username")
    if (
        set(data) != {"username"}
        or not isinstance(username, str)
        or not 1 <= len(username) <= 150
        or username != username.strip()
    ):
        raise Problem("INVALID_REQUEST", 422)
    target = (
        get_user_model()
        .objects.filter(
            username=username, is_active=True, is_staff=False, is_superuser=False
        )
        .first()
    )
    if target is None:
        raise Problem("NOT_FOUND", 404)
    row, created = Membership.objects.get_or_create(
        workspace_id=workspace_id, user=target, defaults={"role": "member"}
    )
    if not created:
        raise Problem("MEMBERSHIP_ALREADY_EXISTS", 409)
    record_audit(user.pk, "membership-granted", row.pk)
    return {"userId": row.user_id, "active": True, "revision": row.revision}


def change_member(user, workspace_id, target_id, data):
    from researchloop.governance.services import record_audit

    lock_administrator(user, workspace_id)
    if set(data) != {"active", "revision"} or type(data["active"]) is not bool:
        raise Problem("INVALID_REQUEST", 422)
    revision = data["revision"]
    if type(revision) is not int or revision < 1:
        raise Problem("INVALID_REQUEST", 422)
    row = (
        Membership.objects.select_for_update()
        .filter(workspace_id=workspace_id, user_id=target_id, role="member")
        .first()
    )
    if row is None:
        raise Problem("NOT_FOUND", 404)
    if row.revision != revision:
        raise Problem("STALE_REVISION", 409, revision=row.revision)
    if row.active != data["active"]:
        row.active = data["active"]
        row.revision += 1
        row.save(update_fields=["active", "revision"])
        record_audit(
            user.pk,
            "membership-granted" if row.active else "membership-revoked",
            row.pk,
        )
    return {"userId": row.user_id, "active": row.active, "revision": row.revision}


def authorize(user, workspace_id=None, administrator=False):
    if not user.is_authenticated or not user.is_active:
        raise Problem("UNAUTHORIZED", 401)
    memberships = Membership.objects.filter(user=user, active=True)
    if workspace_id is not None:
        memberships = memberships.filter(workspace_id=workspace_id)
    member = memberships.order_by("pk").first()
    if member is None or (administrator and member.role != "workspace-admin"):
        raise Problem(
            "NOT_FOUND" if workspace_id else "FORBIDDEN", 404 if workspace_id else 403
        )
    return member


def authorize_actor(actor_id, workspace_id):
    """Resolve a persisted worker actor through Identity, never a cached user."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=actor_id).first()
    if user is None:
        raise Problem("UNAUTHORIZED", 401)
    return authorize(user, workspace_id)


def resolve_authorized_actor(actor_id, workspace_id):
    """Return a freshly resolved actor for an authenticated internal contract."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=actor_id).first()
    if user is None:
        raise Problem("UNAUTHORIZED", 401)
    authorize(user, workspace_id)
    return user
