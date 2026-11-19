import uuid
from django.db import models


class CardSortRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    frozen = models.BooleanField(default=False)
    content = models.JSONField()
    freeze_key = models.CharField(max_length=64, null=True, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["definition_id", "revision"],
                condition=models.Q(frozen=False),
                name="card_mutable_revision_unique",
            )
        ]
