"""Participation owns raw evidence and edit receipts."""

from researchloop.participation.models import (
    Operation,
    ParticipantSession,
    Response,
    StartIntent,
)


def erase_sessions(workspace_id, study_id, method, subject_ids):
    sessions = ParticipantSession.objects.filter(
        version__study__workspace_id=workspace_id,
        version__study_id=study_id,
        version__study__method__in=method,
        pk__in=subject_ids,
    )
    eligible = list(sessions.values_list("pk", flat=True))
    operations = Operation.objects.filter(session_id__in=eligible)
    operations.update(undone_by=None)
    operations.delete()
    StartIntent.objects.filter(session_id__in=eligible).delete()
    Response.objects.filter(session_id__in=eligible).delete()
    sessions.delete()
