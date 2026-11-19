"""C09 owner callback. The Governance coordinator, not U4, activates suppression."""

import hashlib
from contextlib import ExitStack

from django.db import transaction

from researchloop.evidence_delivery import intents, storage
from researchloop.evidence_delivery.models import (
    Candidate,
    DownloadGrant,
    Export,
    Receipt,
    Selection,
)
from researchloop.governance.gate import delivery_gate
from researchloop.governance.services import analysis_guard, check_suppression
from researchloop.interaction.errors import Problem
from django.db.models import Q


def erase_scoped_delivery(workspace_id, study_id, subjects, *, whole_study, custody):
    selected = Selection.objects.filter(workspace_id=workspace_id, study_id=study_id)
    if not whole_study:
        affected = Q(pk__in=[])
        for subject in subjects:
            affected |= Q(contributor_ids__contains=[subject])
        selected = selected.filter(affected)
    jobs = list(Export.objects.select_for_update().filter(selection__in=selected))
    candidates = Candidate.objects.filter(selection__in=selected)
    if jobs:
        if not custody.enter_context(storage.root_writer_lock()):
            raise Problem("EXPORT_PURGE_PENDING", 503)
        for job in jobs:
            storage.remove_job_paths(job)
    ids = [job.pk for job in jobs]
    DownloadGrant.objects.filter(job_id__in=ids).delete()
    Receipt.objects.filter(
        object_id__in=ids + list(candidates.values_list("pk", flat=True))
    ).delete()
    candidates.delete()
    Export.objects.filter(pk__in=ids).delete()
    selected.delete()


def purge(workspace_id, subject, generation, command_id):
    scope, key = f"u4-erase:{workspace_id}", str(command_id)
    protected_subject = hashlib.sha256(subject.encode()).hexdigest()
    with (
        delivery_gate(workspace_id, True),
        ExitStack() as custody,
        transaction.atomic(),
    ):
        receipt, intent = intents.check(0, scope, key, [generation, protected_subject])
        if receipt:
            return receipt.acknowledgement
        analysis_guard(workspace_id, expected_generation=generation, allow_barrier=True)
        _, suppressed = check_suppression(workspace_id)
        if subject not in suppressed:
            raise Problem("SUPPRESSION_REQUIRED", 409)
        selected = Selection.objects.select_for_update().filter(
            workspace_id=workspace_id,
            revoked=False,
            contributor_ids__contains=[subject],
        )
        ids = list(selected.values_list("pk", flat=True))
        jobs = Export.objects.select_for_update().filter(selection_id__in=ids)
        candidates = Candidate.objects.select_for_update().filter(selection_id__in=ids)
        candidate_ids = list(candidates.values_list("pk", flat=True))
        job_ids = [job.pk for job in jobs]
        try:
            no_writers = (
                custody.enter_context(storage.root_writer_lock())
                if any(job.lease_id for job in jobs)
                else True
            )
            for job in jobs:
                storage.remove_job_paths(job)
            if not no_writers:
                # Removing the pathname before it can be promoted is enough
                # to revoke access, but not a successful physical purge yet.
                # The failed command has no receipt. The already-active
                # suppression policy still prevents every download.
                raise Problem("EXPORT_PURGE_PENDING", 503)
        except OSError as error:
            raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503) from error
        Receipt.objects.filter(object_id__in=candidate_ids + job_ids).delete()
        candidates.delete()
        count = jobs.update(state="revoked", artifact_size=0, artifact_digest="")
        Selection.objects.filter(pk__in=ids).update(revoked=True, contributor_ids=[])
        answer = {"exportsRevoked": count, "candidatesRemoved": len(candidate_ids)}
        Receipt.objects.create(
            actor_id=0,
            scope=scope,
            key=key,
            fingerprint=intent,
            object_id=command_id,
            acknowledgement=answer,
        )
        return answer
