"""StudyLifecycle-owned cleanup; retain opaque tombstones for replay."""

from django.db.models import Q

from researchloop.cards.models import CardSortRevision
from researchloop.interaction.errors import Problem
from researchloop.studies.models import MutationReceipt, PreviewSnapshot, Study
from researchloop.trees.models import TreeTestRevision


def erase_definition(workspace_id, study_id):
    study = (
        Study.objects.select_for_update()
        .filter(pk=study_id, workspace_id=workspace_id, state="deleted")
        .first()
    )
    if study is None:
        # Valid restore snapshots can precede creation of a now-deleted study.
        if Study.objects.filter(pk=study_id).exists():
            raise Problem("ERASURE_SCOPE_MISMATCH", 409)
        return
    draft_id = study.draft.pk
    version_ids = list(study.versions.values_list("pk", flat=True))
    references = Q(payload__studyId=str(study_id)) | Q(payload__draftId=str(draft_id))
    for version_id in version_ids:
        references |= Q(payload__versionId=str(version_id))
    MutationReceipt.objects.filter(references).delete()
    PreviewSnapshot.objects.filter(draft_id=draft_id).delete()
    CardSortRevision.objects.filter(definition_id=draft_id).delete()
    TreeTestRevision.objects.filter(definition_id=draft_id).delete()
    study.title = ""
    study.current_version = None
    study.save(update_fields=["title", "current_version"])
