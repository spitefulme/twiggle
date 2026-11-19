import uuid
from django.db import models


class EvidenceClock(models.Model):
    version = models.OneToOneField("studies.PublishedVersion", on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField(default=0)


class ParticipantSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        "studies.PublishedVersion", on_delete=models.PROTECT, related_name="sessions"
    )
    capability_digest = models.CharField(max_length=64, unique=True)
    revision = models.PositiveIntegerField(default=0)
    state = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="active")
    started_at = models.DateTimeField(auto_now_add=True)
    activity_at = models.DateTimeField(auto_now_add=True)
    consent_revision = models.PositiveIntegerField(default=1)


class Operation(models.Model):
    session = models.ForeignKey(ParticipantSession, on_delete=models.PROTECT)
    operation_id = models.CharField(max_length=128)
    sequence = models.PositiveIntegerField()
    digest = models.CharField(max_length=64)
    payload = models.JSONField()
    acknowledgement = models.JSONField()
    prior_revision = models.PositiveIntegerField(default=0)
    resulting_revision = models.PositiveIntegerField(default=0)
    inverse_data = models.JSONField(null=True)
    undone_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True)
    committed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "operation_id"], name="unique_operation"
            ),
            models.UniqueConstraint(
                fields=["session", "sequence"], name="unique_session_sequence"
            ),
        ]


class Response(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(ParticipantSession, on_delete=models.PROTECT)
    version = models.ForeignKey("studies.PublishedVersion", on_delete=models.PROTECT)
    state = models.JSONField()
    revision = models.PositiveIntegerField()
    finalization_key = models.CharField(max_length=128)
    committed_at = models.DateTimeField(auto_now_add=True)


class StartBucket(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    window = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)


class StartIntent(models.Model):
    key_digest = models.CharField(max_length=64, unique=True)
    session = models.ForeignKey(ParticipantSession, on_delete=models.PROTECT)
