import uuid
from django.db import models


class TreeTestRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    frozen = models.BooleanField(default=False)
    content = models.JSONField()
    method_format = models.CharField(max_length=12, default="tree-test", editable=False)
    freeze_key = models.CharField(max_length=64, null=True, unique=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["definition_id", "revision"],
                condition=models.Q(frozen=False),
                name="tree_mutable_revision_unique",
            )
        ]
