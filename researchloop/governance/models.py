import uuid

from django.db import models


class AuditFact(models.Model):
    actor = models.CharField(max_length=100)
    action = models.CharField(max_length=80)
    reference = models.CharField(max_length=100)
    occurred_at = models.DateTimeField(auto_now_add=True)


class Ledger(models.Model):
    workspace = models.OneToOneField("identity.Workspace", on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField(default=0)
    available = models.BooleanField(default=True)
    analysis_generation = models.PositiveBigIntegerField(default=0)
    analysis_barrier = models.BooleanField(default=False)
    u5_head = models.CharField(max_length=64, blank=True, default="")
    u5_epoch = models.PositiveBigIntegerField(default=0)
    u5_quarantined = models.BooleanField(default=False)


class Suppression(models.Model):
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT)
    subject = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ledger", "subject"], name="unique_suppression"
            )
        ]


class InstallationRecord(models.Model):
    """Explicit operator initialization, not inferred from ledger contents."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    mode = models.CharField(
        max_length=20,
        choices=[
            ("fresh-local", "Fresh synthetic local"),
            ("restored", "Restored"),
            ("unknown", "Unknown"),
            ("portable-core", "Portable core"),
        ],
    )
    synthetic_only = models.BooleanField(default=False)
    initialized_at = models.DateTimeField(auto_now_add=True)


class ErasureCase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("identity.Workspace", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12)
    subject = models.UUIDField()
    study_id = models.UUIDField()
    status = models.CharField(max_length=16, default="accepted")
    lifecycle_closed = models.BooleanField(default=False)


class ErasureCommand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        ErasureCase, on_delete=models.PROTECT, related_name="commands"
    )
    owner = models.CharField(max_length=2)
    epoch = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, default="pending")
    attempt = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(null=True)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "epoch", "owner"], name="u5_case_epoch_owner"
            )
        ]


class ErasureAttempt(models.Model):
    command = models.ForeignKey(
        ErasureCommand, on_delete=models.PROTECT, related_name="attempts"
    )
    sequence = models.PositiveSmallIntegerField()
    fence = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=16, default="leased")
    acknowledged_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["command", "sequence"], name="u5_attempt_sequence"
            )
        ]
