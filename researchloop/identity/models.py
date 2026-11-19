from django.conf import settings
from django.db import models


class Workspace(models.Model):
    name = models.CharField(max_length=120)


class Membership(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role = models.CharField(
        max_length=20,
        choices=[("member", "Member"), ("workspace-admin", "Administrator")],
    )
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="unique_membership"
            )
        ]
