from django.core.management.base import BaseCommand, CommandError
from researchloop.governance.readiness import assert_fresh_local
from researchloop.analysis.jobs import worker_available
from researchloop.evidence_delivery.storage import directory

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        report = assert_fresh_local()
        if not worker_available():
            raise CommandError('Analysis heartbeat unavailable')
        directory()
        from researchloop.identity.management.commands.portable_exports import acquire_worker_lock
        import os
        try:
            lock = acquire_worker_lock()
        except BlockingIOError:
            pass
        else:
            os.close(lock)
            raise CommandError('Export worker singleton lock is not held')
        import json
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3) as response:
            if not json.load(response).get('u4GuardedSyncWorker'):
                raise CommandError('Guarded download transport unavailable')
        self.stdout.write(str({**report, 'analysisWorker': True, 'exportWorker': True, 'guardedDownloads': True, 'privateArtifacts': True}))
