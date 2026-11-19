"""Studies-owned authorized immutable-version metadata, copied across the port."""

import copy
from dataclasses import dataclass

from researchloop.interaction.errors import Problem
from researchloop.studies.models import PublishedVersion
from researchloop.studies.services import get_study


@dataclass(frozen=True)
class AnalysisVersion:
    study_id: object
    version_id: object
    workspace_id: int
    frozen_revision_id: object
    method: str
    content: dict


def authorized_version(user, study_id, version_id, *, lock=False):
    study = get_study(user, study_id, lock)
    versions = PublishedVersion.objects.filter(study=study, pk=version_id)
    if lock:
        versions = versions.select_for_update()
    version = versions.first()
    if version is None:
        raise Problem("NOT_FOUND", 404)
    return AnalysisVersion(
        study.pk,
        version.pk,
        study.workspace_id,
        version.frozen_revision,
        study.method,
        copy.deepcopy(version.content),
    )
