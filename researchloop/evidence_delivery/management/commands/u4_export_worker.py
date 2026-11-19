from time import sleep

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from researchloop.evidence_delivery.jobs import run_once


class Command(BaseCommand):
    help = "Own the isolated U4 evidence export lane."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--workspace-id", required=True, type=int)

    def handle(self, *args, **options):
        if options["once"]:
            run_once(options["workspace_id"])
            return
        while True:
            close_old_connections()
            had_work = run_once(options["workspace_id"])
            if not had_work:
                sleep(0.25)
