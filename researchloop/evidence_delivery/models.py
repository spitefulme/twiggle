import uuid

from django.db import models


class Selection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.PositiveBigIntegerField()
    workspace_id = models.PositiveBigIntegerField()
    study_id = models.UUIDField()
    version_id = models.UUIDField()
    analysis_id = models.UUIDField()
    cohort_id = models.UUIDField()
    filter_status = models.CharField(max_length=16, blank=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    contributor_ids = models.JSONField(default=list)
    generation = models.PositiveBigIntegerField()
    revoked = models.BooleanField(default=False)


class Export(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    selection = models.ForeignKey(Selection, on_delete=models.PROTECT)
    actor_id = models.PositiveBigIntegerField()
    format = models.CharField(max_length=8)
    state = models.CharField(max_length=16, default="queued")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    lease_id = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    artifact_size = models.PositiveBigIntegerField(default=0)
    artifact_digest = models.CharField(max_length=64, blank=True)


class Candidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    selection = models.ForeignKey(Selection, on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField(default=1)
    topology = models.JSONField()


class DownloadGrant(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    job = models.ForeignKey(Export, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)


class Receipt(models.Model):
    actor_id = models.PositiveBigIntegerField()
    scope = models.CharField(max_length=110)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    object_id = models.UUIDField()
    acknowledgement = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor_id", "scope", "key"], name="u4_exact_intent"
            )
        ]
