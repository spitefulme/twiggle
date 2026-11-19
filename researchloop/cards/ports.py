"""C02 typed definition boundary; only CardSortDefinition reads its own rows."""

import copy
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID
from django.db import transaction
from researchloop.cards.models import CardSortRevision
from researchloop.cards.services import validate
from researchloop.interaction.errors import Problem


@dataclass(frozen=True)
class DraftReference:
    draft_id: UUID
    revision_id: UUID
    expected_revision: int


def create_revision(draft_id, revision, content):
    row = CardSortRevision.objects.create(
        definition_id=draft_id, revision=revision, content=copy.deepcopy(content)
    )
    return row.pk


def read_draft(reference):
    row = CardSortRevision.objects.filter(
        pk=reference.revision_id,
        definition_id=reference.draft_id,
        revision=reference.expected_revision,
        frozen=False,
    ).first()
    if row is None:
        raise Problem("INVALID_METHOD_REFERENCE", 409)
    return copy.deepcopy(row.content)


def validate_draft(reference):
    validate(read_draft(reference))
    return {"valid": True, "fieldErrors": []}


@transaction.atomic
def freeze_for_publish(reference, key):
    content = read_draft(reference)
    validate(content)
    freeze_key = hashlib.sha256((str(reference.draft_id) + key).encode()).hexdigest()
    row, created = CardSortRevision.objects.get_or_create(
        freeze_key=freeze_key,
        defaults={
            "definition_id": reference.draft_id,
            "revision": reference.expected_revision,
            "content": copy.deepcopy(content),
            "frozen": True,
        },
    )
    if not created and (
        row.revision != reference.expected_revision or row.content != content
    ):
        raise Problem("IDEMPOTENCY_CONFLICT", 409)
    return {
        "frozenRevisionId": row.pk,
        "integrityDigest": hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest(),
    }


def read_frozen(revision_id, definition_id):
    row = CardSortRevision.objects.filter(
        pk=revision_id, definition_id=definition_id, frozen=True
    ).first()
    if row is None:
        raise Problem("INVALID_METHOD_REFERENCE", 409)
    return copy.deepcopy(row.content)
