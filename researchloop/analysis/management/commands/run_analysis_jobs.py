"""Explicit foreground operator-owned process; never installs a host service."""

import json
import os
import signal
import threading
import uuid
import hashlib
import marshal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from researchloop.analysis import jobs
from researchloop.analysis import cards, services
from researchloop.participation import evidence, sort_state
from researchloop.analysis.models import AnalysisJob
from researchloop.interaction.errors import Problem


class Command(BaseCommand):
    help = "Run one project-local lease-fenced analysis worker in the foreground."

    def add_arguments(self, parser):
        parser.add_argument("--poll-interval", type=float, default=1)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--max-jobs", type=int, default=0)
        parser.add_argument("--max-idle-polls", type=int, default=0)

    def handle(self, *args, **options):
        interval, maximum, idle_limit = (
            options["poll_interval"],
            options["max_jobs"],
            options["max_idle_polls"],
        )
        if (
            not 0.1 <= interval <= 1
            or not 0 <= maximum <= 64
            or not 0 <= idle_limit <= 3600
        ):
            raise CommandError("Poll/finite worker bounds invalid")
        identity = str(uuid.uuid4())
        stop = threading.Event()
        handlers = {
            number: signal.getsignal(number)
            for number in [signal.SIGTERM, signal.SIGINT]
        }

        def terminate(number, frame):
            stop.set()  # Drain the current bounded job; do not claim another.

        for number in handlers:
            signal.signal(number, terminate)

        def emit(event, **data):
            self.stdout.write(
                json.dumps(
                    {"event": event, "workerId": identity, "pid": os.getpid(), **data}
                )
            )
            self.stdout.flush()

        completed = idle = 0
        registered = False
        try:
            jobs.heartbeat(identity)
            registered = True
            emit(
                "started",
                pollInterval=interval,
                maxJobs=maximum,
                maxIdlePolls=idle_limit,
                startupSourceHashes={
                    name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                    for name in [
                        "researchloop/analysis/cards.py",
                        "researchloop/analysis/services.py",
                        "researchloop/analysis/jobs.py",
                        "researchloop/participation/evidence.py",
                        "researchloop/participation/sort_state.py",
                        "researchloop/analysis/management/commands/run_analysis_jobs.py",
                    ]
                },
                loadedFunctionCodeHashes={
                    name: hashlib.sha256(marshal.dumps(function.__code__)).hexdigest()
                    for name, function in {
                        "compute_matrices": cards.compute_matrices,
                        "average_linkage": cards.average_linkage,
                        "calculate": services.calculate,
                        "execute": jobs.execute,
                        "analysis_snapshot": evidence.analysis_snapshot,
                        "complete_validated": sort_state.complete_validated,
                    }.items()
                },
            )
            while not stop.is_set():
                try:
                    jobs.heartbeat(identity)
                    jobs.expire_due()
                    claimed = jobs.claim(identity)
                    if claimed:
                        emit(
                            "claimed",
                            jobId=str(claimed.job_id),
                            attempt=claimed.attempt,
                            fence=claimed.fence,
                        )
                        succeeded = jobs.execute(claimed, identity)
                        completed += 1
                        idle = 0
                        row = AnalysisJob.objects.filter(pk=claimed.job_id).first()
                        emit(
                            "completed",
                            jobId=str(claimed.job_id),
                            succeeded=succeeded,
                            state=row.status if row else "erased",
                            errorCode=row.error_code if row else "ANALYSIS_FENCED",
                        )
                    else:
                        idle += 1
                    if (
                        options["once"]
                        or (maximum and completed >= maximum)
                        or (idle_limit and idle >= idle_limit)
                    ):
                        break
                except Problem as error:
                    emit("unavailable", code=error.code)
                    if options["once"] or error.code == "ANALYSIS_WORKER_BUSY":
                        raise CommandError(error.code) from error
                stop.wait(interval)
        finally:
            if registered:
                try:
                    jobs.heartbeat(identity, active=False)
                except Exception as error:
                    emit("heartbeat-unavailable", errorType=type(error).__name__)
            emit("stopped", completed=completed, draining=stop.is_set())
            for number, handler in handlers.items():
                signal.signal(number, handler)
