import re
from contextlib import ExitStack
from time import monotonic

from django.db import transaction
from django.http import StreamingHttpResponse
from django.utils import timezone

from researchloop.evidence_delivery import jobs, selection, storage, transport
from researchloop.evidence_delivery.models import DownloadGrant
from researchloop.governance.gate import delivery_gate
from researchloop.identity.services import resolve_authorized_actor
from researchloop.interaction.errors import Problem


TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
CONTENT_TYPES = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "svg": "image/svg+xml",
}


class DownloadBody:
    """Django registers close() even if the HTTP iterator never starts."""

    def __init__(self, file, resources):
        self.file = file
        self.resources = resources

    def __iter__(self):
        try:
            while block := self.file.read(65536):
                yield block
        finally:
            self.close()

    def close(self):
        self.resources.close()


def response(token):
    if not TOKEN_PATTERN.fullmatch(token):
        raise Problem("DOWNLOAD_FORBIDDEN", 403)
    grant_id = jobs.digest(token)
    workspace_id = (
        DownloadGrant.objects.filter(pk=grant_id)
        .values_list("job__selection__workspace_id", flat=True)
        .first()
    )
    if workspace_id is None:
        raise Problem("DOWNLOAD_FORBIDDEN", 403)
    socket = transport.request_socket()
    if socket is None:
        raise Problem("DOWNLOAD_TRANSPORT_UNAVAILABLE", 503)
    resources = ExitStack()
    try:
        resources.enter_context(delivery_gate(workspace_id, download=True))
        with transaction.atomic():
            grant = (
                DownloadGrant.objects.select_related("job__selection")
                .filter(pk=grant_id)
                .first()
            )
            if grant is None or grant.expires_at <= timezone.now():
                raise Problem("DOWNLOAD_GONE", 410)
            job = grant.job
            selected = job.selection
            if job.state != "completed" or selected.revoked:
                raise Problem("DOWNLOAD_GONE", 410)
            remaining = (
                min(grant.expires_at, jobs.available_until(job)) - timezone.now()
            ).total_seconds()
            if remaining <= 0:
                raise Problem("DOWNLOAD_GONE", 410)
            socket.deadline = min(socket.deadline, monotonic() + remaining)
            try:
                user = resolve_authorized_actor(job.actor_id, workspace_id)
                selection.current(user, selected, hydrate=False)
            except Problem as error:
                if error.code in {"SUPPRESSED", "COHORT_STALE"}:
                    raise Problem("DOWNLOAD_GONE", 410) from error
                if error.status in {401, 403, 404}:
                    raise Problem("DOWNLOAD_FORBIDDEN", 403) from error
                raise
            try:
                file = resources.enter_context(storage.open_artifact(job))
            except OSError as error:
                raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503) from error
        result = StreamingHttpResponse(
            DownloadBody(file, resources), content_type=CONTENT_TYPES[job.format]
        )
        result["Content-Length"] = str(job.artifact_size)
        result["Content-Disposition"] = (
            f'attachment; filename="study-evidence.{job.format}"'
        )
        result["X-Artifact-SHA256"] = job.artifact_digest
        return result
    except BaseException:
        resources.close()
        raise
