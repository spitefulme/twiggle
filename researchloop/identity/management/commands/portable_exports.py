"""One installer-owned export process, visiting authorized workspace queues."""
import signal
import threading
import fcntl
import os
import stat
from django.core.management.base import BaseCommand, CommandError
from researchloop.evidence_delivery.storage import directory

def acquire_worker_lock():
    path = directory() / '.portable-export-worker.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise CommandError('Invalid export singleton lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd
from django.db import close_old_connections
from researchloop.identity.models import Workspace
from researchloop.evidence_delivery.jobs import run_once

class Command(BaseCommand):
    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            lock = acquire_worker_lock()
        except BlockingIOError:
            raise CommandError('Another portable export worker owns this installation') from None
        try:
            while not stop.is_set():
                close_old_connections()
                for workspace_id in Workspace.objects.order_by('pk').values_list('pk', flat=True).iterator():
                    if stop.is_set():
                        break
                    run_once(workspace_id)
                stop.wait(0.25)
        finally:
            os.close(lock)
