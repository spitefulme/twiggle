import copy
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.conf import settings
from django.utils import timezone

from researchloop.cards import services as cards
from researchloop.cards import ports as card_ports
from researchloop.governance.services import record_audit
from researchloop.identity.models import Workspace
from researchloop.identity.services import authorize
from researchloop.interaction.errors import Problem
from researchloop.studies.models import (
    PreviewSnapshot,
    PublishedVersion,
    Study,
    StudyDraft,
)
from researchloop.trees import services as trees
from researchloop.trees import ports as tree_ports


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def recruitment_token(study_id):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        ("recruitment:" + str(study_id)).encode(),
        hashlib.sha256,
    ).hexdigest()


def get_study(user, study_id, lock=False):
    query = Study.objects.select_for_update() if lock else Study.objects
    study = query.filter(pk=study_id).exclude(state="deleted").first()
    if study is None:
        raise Problem("NOT_FOUND", 404)
    authorize(user, study.workspace_id)
    return study


def draft_data(study):
    draft = study.draft
    return {
        "studyId": str(study.pk),
        "draftId": str(draft.pk),
        "method": study.method,
        "title": study.title,
        "revision": draft.revision,
        "content": draft.content,
        "lifecycleState": study.state,
        "currentVersionId": str(study.current_version_id)
        if study.current_version_id
        else None,
        "versions": [
            {
                "versionId": str(v.pk),
                "versionNumber": v.number,
                "frozenRevisionId": str(v.frozen_revision),
                "state": v.state,
            }
            for v in study.versions.order_by("number")
        ],
    }


@transaction.atomic
def create(user, data):
    membership = authorize(user)
    Workspace.objects.select_for_update().get(pk=membership.workspace_id)
    authorize(user, membership.workspace_id)
    if Study.objects.filter(workspace_id=membership.workspace_id).count() >= 100:
        raise Problem("LIMIT_EXCEEDED", 409)
    if (
        set(data) != {"method", "title"}
        or data["method"] not in {"card-sort", "tree-test", "tree-test-v2"}
        or not isinstance(data["title"], str)
        or not 1 <= len(data["title"]) <= 120
    ):
        raise Problem("INVALID_DEFINITION")
    study = Study.objects.create(
        workspace_id=membership.workspace_id,
        method=data["method"],
        title=data["title"],
        recruitment_digest=digest(secrets.token_urlsafe(32)),
    )
    study.recruitment_digest = digest(recruitment_token(study.pk))
    study.save(update_fields=["recruitment_digest"])
    draft = StudyDraft.objects.create(study=study)
    draft.method_revision = create_definition_revision(study, draft, {})
    draft.save(update_fields=["method_revision"])
    record_audit(user.pk, "study-created", study.pk)
    return draft_data(study)


def validator(study):
    return {
        "card-sort": cards.validate,
        "tree-test": trees.validate,
        "tree-test-v2": trees.validate_v2,
    }[study.method]


def create_definition_revision(study, draft, content):
    if study.method == "tree-test-v2":
        return tree_ports.create_revision(
            draft.pk, draft.revision, content, method_format=study.method
        )
    return definition_port(study).create_revision(draft.pk, draft.revision, content)


def definition_port(study):
    return card_ports if study.method == "card-sort" else tree_ports


def check_revision(draft, expected):
    if type(expected) is not int or draft.revision != expected:
        raise Problem("STALE_REVISION", 409, revision=draft.revision)


@transaction.atomic
def save(user, study_id, data, expected):
    study = get_study(user, study_id, True)
    draft = study.draft
    check_revision(draft, expected)
    if study.method == "tree-test-v2" and set(data) == {"changes"}:
        data = {"content": trees.patch_v2(draft.content, data["changes"])}
    if set(data) != {"content"} or not isinstance(data["content"], dict):
        raise Problem("INVALID_DEFINITION")
    validator(study)(data["content"])
    draft.revision += 1
    draft.method_revision = create_definition_revision(study, draft, data["content"])
    draft.save(update_fields=["method_revision", "revision"])
    record_audit(user.pk, "draft-saved", draft.pk)
    return draft_data(study)


@transaction.atomic
def preview(user, study_id, data, expected):
    study = get_study(user, study_id, True)
    draft = study.draft
    check_revision(draft, expected)
    if data != {"draftId": str(draft.pk)}:
        raise Problem("INVALID_DEFINITION")
    if study.method == "tree-test-v2":
        trees.validate_v2(draft.content, publishing=True)
    else:
        validator(study)(draft.content)
    snapshot = PreviewSnapshot.objects.create(
        draft=draft,
        revision=draft.revision,
        content=copy.deepcopy(draft.content),
        researcher=user,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    record_audit(user.pk, "preview-created", snapshot.pk)
    return {
        "previewId": str(snapshot.pk),
        "draftId": str(draft.pk),
        "expiresAt": snapshot.expires_at.isoformat(),
    }


@transaction.atomic
def publish(user, study_id, data, expected, key=None):
    study = get_study(user, study_id, True)
    draft = study.draft
    check_revision(draft, expected)
    if data != {"draftId": str(draft.pk)}:
        raise Problem("INVALID_DEFINITION")
    port = definition_port(study)
    reference = port.DraftReference(draft.pk, draft.method_revision, draft.revision)
    port.validate_draft(reference)
    number = study.versions.count() + 1
    if number > 10:
        raise Problem("LIMIT_EXCEEDED", 409)
    if study.current_version_id:
        previous = PublishedVersion.objects.select_for_update().get(
            pk=study.current_version_id
        )
        drain(previous)
    frozen = port.freeze_for_publish(reference, key or secrets.token_urlsafe(24))
    version = PublishedVersion.objects.create(
        study=study, number=number, frozen_revision=frozen["frozenRevisionId"]
    )
    token = recruitment_token(study.pk)
    study.recruitment_digest = digest(token)
    study.current_version = version
    study.state = "open"
    study.save(update_fields=["recruitment_digest", "current_version", "state"])
    record_audit(user.pk, "version-published", version.pk)
    return {
        "versionId": str(version.pk),
        "versionNumber": number,
        "frozenRevisionId": str(version.frozen_revision),
        "publishedAt": version.published_at.isoformat(),
        "recruitmentLink": "/recruit/" + token,
    }


def drain(version):
    if version.state == "revoked":
        return
    deadline = timezone.now() + timedelta(hours=24)
    version.state = "draining"
    version.write_deadline = (
        min(version.write_deadline, deadline) if version.write_deadline else deadline
    )
    version.save(update_fields=["state", "write_deadline"])


@transaction.atomic
def lifecycle(user, study_id, data):
    study = get_study(user, study_id, True)
    if set(data) != {"action"} or data["action"] not in {"close", "revoke"}:
        raise Problem("UNSUPPORTED_LIFECYCLE")
    for version in study.versions.select_for_update().order_by("number"):
        if data["action"] == "revoke":
            version.state = "revoked"
            version.save(update_fields=["state"])
        else:
            drain(version)
    study.state = "revoked" if data["action"] == "revoke" else "closed"
    study.save(update_fields=["state"])
    record_audit(user.pk, study.state, study.pk)
    return {
        "studyId": str(study.pk),
        "state": study.state,
        "revision": study.draft.revision,
        "closeAt": timezone.now().isoformat() if study.state == "closed" else None,
        "revokeAt": timezone.now().isoformat() if study.state == "revoked" else None,
    }
