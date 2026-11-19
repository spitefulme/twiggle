"""Trusted operator-owned local U5 work lane, no public callback."""

from datetime import timedelta
from time import sleep

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from researchloop.governance.coordinator import admit_expiry, run_one
from researchloop.governance.models import ErasureCase
from researchloop.interaction.errors import Problem
from researchloop.participation.models import ParticipantSession, Response


def _admit_due(workspace_id):
    now = timezone.now()
    known = ErasureCase.objects.filter(workspace_id=workspace_id, kind="expiry").values(
        "subject"
    )
    submissions = Response.objects.filter(session_id=OuterRef("pk")).values(
        "committed_at"
    )[:1]
    due = (
        ParticipantSession.objects.filter(version__study__workspace_id=workspace_id)
        .exclude(pk__in=known)
        .exclude(version__study__state="deleted")
        .annotate(candidate_clock=Coalesce(Subquery(submissions), "activity_at"))
        .filter(candidate_clock__lte=now - timedelta(days=365))
        .order_by("candidate_clock", "pk")
        .values_list("pk", "revision")[:16]
    )
    for item, revision in tuple(due):
        admit_expiry(workspace_id, item, revision=revision, now=now)


class Command(BaseCommand):
    help = "Run the isolated U5 workspace retention and erasure lane."

    def add_arguments(self, parser):
        parser.add_argument("--workspace-id", required=True, type=int)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--recovery-epoch", type=int)

    def handle(self, *args, **options):
        workspace = options["workspace_id"]
        epoch = options["recovery_epoch"]
        if workspace < 1 or (epoch is not None and epoch < 1):
            raise CommandError("Invalid isolated workspace or recovery epoch")
        while True:
            close_old_connections()
            try:
                worked = run_one(workspace, recovery_epoch=epoch)
                if not worked and epoch is None:
                    _admit_due(workspace)
            except Problem:
                raise CommandError(
                    "U5 ownership or recovery evidence is unavailable"
                ) from None
            if options["once"]:
                return
            if not worked:
                sleep(0.5)
