"""Analysis-owned derived snapshots and append-only annotation history."""

import uuid

from django.db import models
from django.contrib.postgres.indexes import GinIndex


class AnnotationState(models.Model):
    version_id = models.UUIDField(unique=True)
    workspace_id = models.PositiveBigIntegerField()
    revision = models.PositiveBigIntegerField(default=0)
    coding = models.JSONField(default=dict)
    exclusions = models.JSONField(default=dict)


class AnnotationEvent(models.Model):
    version_id = models.UUIDField()
    workspace_id = models.PositiveBigIntegerField()
    actor_id = models.CharField(max_length=100)
    revision = models.PositiveBigIntegerField()
    response_id = models.UUIDField()
    subject_reference = models.CharField(max_length=36, default="")
    kind = models.CharField(max_length=16)
    group_id = models.CharField(max_length=24, null=True)
    payload = models.JSONField()
    key = models.CharField(max_length=128)
    digest = models.CharField(max_length=64)
    acknowledgement = models.JSONField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version_id", "revision"], name="analysis_annotation_revision"
            ),
            models.UniqueConstraint(
                fields=["version_id", "actor_id", "key"],
                name="analysis_annotation_intent",
            ),
        ]


class AnalysisCohort(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace_id = models.PositiveBigIntegerField()
    study_id = models.UUIDField()
    version_id = models.UUIDField()
    frozen_revision_id = models.UUIDField()
    evidence_revision = models.PositiveBigIntegerField()
    annotation_revision = models.PositiveBigIntegerField()
    suppression_revision = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    included_response_ids = models.JSONField(default=list)
    exclusion_reasons = models.JSONField(default=dict)
    counts = models.JSONField()
    snapshot = models.JSONField()
    subject_references = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version_id", "fingerprint"], name="analysis_cohort_snapshot"
            )
        ]
        indexes = [
            GinIndex(fields=["subject_references"], name="analysis_subjects_gin")
        ]


class AnalysisResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cohort = models.OneToOneField(AnalysisCohort, on_delete=models.PROTECT)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class AnalysisQueueGuard(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)


class WorkerHeartbeat(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    identity = models.UUIDField()
    process_id = models.PositiveIntegerField()
    seen_at = models.DateTimeField()
    active = models.BooleanField(default=True)


class AnalysisJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace_id = models.PositiveBigIntegerField()
    actor_id = models.PositiveBigIntegerField()
    version_id = models.UUIDField()
    cohort = models.ForeignKey(AnalysisCohort, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default="queued")
    progress = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=80, default="")
    admitted_at = models.DateTimeField()
    deadline_at = models.DateTimeField()
    lease_owner = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    attempt = models.PositiveIntegerField(default=0)
    fence = models.PositiveBigIntegerField(default=0)
    deletion_generation = models.PositiveBigIntegerField()
    input_revision = models.CharField(max_length=64)
    cancel_requested = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True)


class AnalysisRequestReceipt(models.Model):
    actor_id = models.PositiveBigIntegerField()
    version_id = models.UUIDField()
    key = models.CharField(max_length=128)
    digest = models.CharField(max_length=64)
    job = models.ForeignKey(AnalysisJob, on_delete=models.PROTECT)
    acknowledgement = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor_id", "version_id", "key"], name="analysis_request_intent"
            )
        ]


class DerivedCleanupReceipt(models.Model):
    workspace_id = models.PositiveBigIntegerField()
    generation = models.PositiveBigIntegerField()
    subject_digest = models.CharField(max_length=64)
    counts = models.JSONField(default=dict)
    acknowledgement = models.JSONField(default=dict)
    completed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace_id", "generation", "subject_digest"],
                name="analysis_cleanup_generation",
            )
        ]
