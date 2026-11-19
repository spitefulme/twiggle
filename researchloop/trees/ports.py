"""C03 typed definition boundary; private targets never leave trusted authoring ports."""

import copy
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID
from django.db import transaction
from researchloop.trees.models import TreeTestRevision
from researchloop.trees.services import validate, validate_v2
from researchloop.interaction.errors import Problem


@dataclass(frozen=True)
class DraftReference:
    draft_id: UUID
    revision_id: UUID
    expected_revision: int


def create_revision(draft_id, revision, content, *, method_format="tree-test"):
    row = TreeTestRevision.objects.create(
        definition_id=draft_id,
        revision=revision,
        content=copy.deepcopy(content),
        method_format=method_format,
    )
    return row.pk


def _read_draft_row(reference):
    row = TreeTestRevision.objects.filter(
        pk=reference.revision_id,
        definition_id=reference.draft_id,
        revision=reference.expected_revision,
        frozen=False,
    ).first()
    if row is None:
        raise Problem("INVALID_METHOD_REFERENCE", 409)
    return row


def read_draft(reference):
    return copy.deepcopy(_read_draft_row(reference).content)


def _validate_publication(row):
    if row.method_format == "tree-test-v2":
        validate_v2(row.content, publishing=True)
    else:
        validate(row.content)


def validate_draft(reference):
    _validate_publication(_read_draft_row(reference))
    return {"valid": True, "fieldErrors": []}


@transaction.atomic
def freeze_for_publish(reference, key):
    draft = _read_draft_row(reference)
    content = draft.content
    _validate_publication(draft)
    freeze_key = hashlib.sha256((str(reference.draft_id) + key).encode()).hexdigest()
    row, created = TreeTestRevision.objects.get_or_create(
        freeze_key=freeze_key,
        defaults={
            "definition_id": reference.draft_id,
            "revision": reference.expected_revision,
            "content": copy.deepcopy(content),
            "frozen": True,
            "method_format": draft.method_format,
        },
    )
    if not created and (
        row.revision != reference.expected_revision
        or row.content != content
        or row.method_format != draft.method_format
    ):
        raise Problem("IDEMPOTENCY_CONFLICT", 409)
    return {
        "frozenRevisionId": row.pk,
        "integrityDigest": hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest(),
    }


def read_frozen(revision_id, definition_id):
    row = TreeTestRevision.objects.filter(
        pk=revision_id, definition_id=definition_id, frozen=True
    ).first()
    if row is None:
        raise Problem("INVALID_METHOD_REFERENCE", 409)
    return copy.deepcopy(row.content)
