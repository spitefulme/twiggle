import uuid

from django.db import models


class Study(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("identity.Workspace", on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    method = models.CharField(max_length=12)
    recruitment_digest = models.CharField(max_length=64, unique=True)
    current_version = models.ForeignKey(
        "PublishedVersion",
        null=True,
        on_delete=models.PROTECT,
        related_name="current_studies",
    )
    state = models.CharField(max_length=12, default="draft")


class StudyDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    study = models.OneToOneField(Study, on_delete=models.PROTECT, related_name="draft")
    revision = models.PositiveIntegerField(default=1)
    method_revision = models.UUIDField(default=uuid.uuid4)

    @property
    def content(self):
        from researchloop.studies.services import definition_port

        port = definition_port(self.study)
        return port.read_draft(
            port.DraftReference(self.pk, self.method_revision, self.revision)
        )


class PublishedVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    study = models.ForeignKey(Study, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    frozen_revision = models.UUIDField(default=uuid.uuid4, editable=False)
    published_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=12, default="open")
    write_deadline = models.DateTimeField(null=True)

    @property
    def content(self):
        from researchloop.studies.services import definition_port

        return definition_port(self.study).read_frozen(
            self.frozen_revision, self.study.draft.pk
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study", "number"], name="unique_version_number"
            )
        ]


class PreviewSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(StudyDraft, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    content = models.JSONField()
    expires_at = models.DateTimeField()
    researcher = models.ForeignKey("auth.User", on_delete=models.PROTECT)


class MutationReceipt(models.Model):
    actor = models.CharField(max_length=100)
    key = models.CharField(max_length=128)
    digest = models.CharField(max_length=64)
    payload = models.JSONField()
    status = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "key"], name="unique_mutation_receipt"
            )
        ]
