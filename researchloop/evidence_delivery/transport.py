"""Bound actual protected sync-worker socket writes by one absolute deadline."""

from contextvars import ContextVar
from contextlib import suppress
from copy import copy
from time import monotonic
from urllib.parse import parse_qsl, unquote

from gunicorn.workers.sync import SyncWorker


_active = ContextVar("u4_protected_socket", default=None)
_worker = ContextVar("u4_owned_worker", default=False)


def request_socket():
    return _active.get()


def worker_ready():
    return _worker.get()


def download_path(path):
    return unquote(path).startswith("/api/v1/downloads/")


def protected_path(path, query="", selector=""):
    parts = unquote(path).rstrip("/").split("/")
    if parts[:3] != ["", "api", "v1"] or len(parts) < 4:
        return False
    if parts[3] in {"exports", "downloads", "candidate-ias"}:
        return True
    if len(parts) != 6 or parts[3] != "studies":
        return False
    if parts[5] == "exports":
        return True
    return parts[5] == "results" and (
        selector == "request"
        or any(key == "status" for key, _ in parse_qsl(query, keep_blank_values=True))
    )


class BoundedSocket:
    def __init__(self, client):
        self.client = client
        self.deadline = monotonic() + 60
        self.previous_timeout = client.gettimeout()

    def __getattr__(self, attr):
        return getattr(self.client, attr)

    def sendall(self, data, *args):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Protected transfer elapsed")
        self.client.settimeout(remaining)
        return self.client.sendall(data, *args)

    def restore(self):
        if self.client.fileno() >= 0:
            self.client.settimeout(self.previous_timeout)


class GuardedSyncWorker(SyncWorker):
    def init_process(self):
        regular_access = self.log.access

        def filtered_access(response, request, environ, elapsed):
            if not download_path(request.path):
                regular_access(response, request, environ, elapsed)

        self.log.access = filtered_access
        return super().init_process()

    def handle_error(self, req, client, addr, exc):
        if req is not None and download_path(req.path):
            req = copy(req)
            req.uri = "/api/v1/downloads/[redacted]"
        return super().handle_error(req, client, addr, exc)

    def handle_request(self, listener, req, client, addr):
        if self.cfg.threads != 1:
            raise RuntimeError("Protected delivery requires one thread per sync worker")
        worker_marker = _worker.set(True)
        try:
            if not protected_path(
                req.path, req.query, dict(req.headers).get("EVIDENCE-SELECTION", "")
            ):
                return super().handle_request(listener, req, client, addr)
            protected = BoundedSocket(client)
            marker = _active.set(protected)
            try:
                return super().handle_request(listener, req, protected, addr)
            except TimeoutError:
                with suppress(OSError):
                    client.shutdown(2)
                return None
            finally:
                _active.reset(marker)
                protected.restore()
        finally:
            _worker.reset(worker_marker)
