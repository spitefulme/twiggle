import copy

from django.db import transaction

from researchloop.analysis import delivery, ports
from researchloop.evidence_delivery import handoff, intents, selection
from researchloop.evidence_delivery.models import Candidate
from researchloop.interaction.errors import Problem
from researchloop.interaction.thin_bounds import identifier
from researchloop.trees.services import validate_v2


def serialize(candidate):
    return {
        "candidateIaId": str(candidate.pk),
        "revision": candidate.revision,
        "sourceVersionId": str(candidate.selection.version_id),
        "sourceAnalysisId": str(candidate.selection.analysis_id),
        "editableTopology": candidate.topology,
    }


def _starting_topology(proof):
    source = proof.public.content
    if proof.public.method == "tree-test-v2":
        return {
            "resources": copy.deepcopy(source["resources"]),
            "occurrences": copy.deepcopy(source["occurrences"]),
        }
    cards = source["cards"]
    resources = [{"resourceId": row["cardId"], "label": row["label"]} for row in cards]
    linkage = proof.result.payload.get("dendrogram")
    branches = []
    if linkage:
        nodes = {row["nodeId"]: row for row in linkage["nodes"]}
        root = nodes[linkage["rootNodeId"]]
        branches = [
            nodes[node_id]["memberCardIds"]
            for node_id in (root["leftNodeId"], root["rightNodeId"])
            if node_id is not None
        ]
    occurrences = []
    if branches:
        # Copy the two exact top-level branches of the completed linkage,
        # rather than re-cluster or copy arbitrary full-depth merge trees.
        for index, members in enumerate(branches):
            group = f"candidate-cluster-{index + 1}"
            occurrences.append(
                {
                    "occurrenceId": group,
                    "kind": "heading",
                    "displayLabel": f"Cluster {index + 1}",
                    "parentOccurrenceId": None,
                    "order": index,
                }
            )
            for position, card_id in enumerate(members):
                occurrences.append(
                    {
                        "occurrenceId": f"candidate-card-{len(occurrences)}",
                        "kind": "resource",
                        "resourceId": card_id,
                        "parentOccurrenceId": group,
                        "order": position,
                    }
                )
    else:
        occurrences = [
            {
                "occurrenceId": f"candidate-card-{index + 1}",
                "kind": "resource",
                "resourceId": row["cardId"],
                "parentOccurrenceId": None,
                "order": index,
            }
            for index, row in enumerate(cards)
        ]
    return {"resources": resources, "occurrences": occurrences}


def create(user, data, key, *, resources=None):
    if set(data) != {"sourceVersionId", "sourceAnalysisId"}:
        raise Problem("INVALID_REQUEST", 422)
    version_id, result_id = data["sourceVersionId"], data["sourceAnalysisId"]
    study_id = delivery.source_study(version_id, result_id)
    public = ports.authorized_version(user, study_id, version_id)
    scope = "candidate-create"
    with handoff.scope(public.workspace_id, resources), transaction.atomic():
        proof = delivery.from_result(user, study_id, version_id, result_id=result_id)
        existing, intent = intents.check(user.pk, scope, key, data)
        if existing:
            return existing.acknowledgement
        selected = selection.capture(user, proof)
        candidate = Candidate.objects.create(
            selection=selected, topology=_starting_topology(proof)
        )
        answer = serialize(candidate)
        intents.remember(user.pk, scope, key, intent, candidate, answer)
        return answer


def _apply_operations(topology, actions):
    amended = copy.deepcopy(topology)
    resources = {row["resourceId"]: row for row in amended["resources"]}
    placements = {row["occurrenceId"]: row for row in amended["occurrences"]}
    for action in actions:
        if not isinstance(action, dict):
            raise Problem("INVALID_REQUEST", 422)
        if action.get("type") == "renameResource" and set(action) == {
            "type",
            "resourceId",
            "label",
        }:
            resource = resources.get(identifier(action["resourceId"]))
            if resource is None:
                raise Problem("INVALID_TOPOLOGY", 422)
            resource["label"] = action["label"]
        elif action.get("type") == "moveOccurrence" and set(action) == {
            "type",
            "occurrenceId",
            "parentOccurrenceId",
            "position",
        }:
            node = placements.get(identifier(action["occurrenceId"]))
            parent = action["parentOccurrenceId"]
            if node is None or (
                parent is not None and identifier(parent) not in placements
            ):
                raise Problem("INVALID_TOPOLOGY", 422)
            old_parent = node.get("parentOccurrenceId")
            destination = _siblings(placements, parent, without=node)
            position = action["position"]
            if type(position) is not int or not 0 <= position <= len(destination):
                raise Problem("INVALID_ORDER", 422)
            if old_parent != parent:
                for index, child in enumerate(
                    _siblings(placements, old_parent, without=node)
                ):
                    child["order"] = index
            node["parentOccurrenceId"] = parent
            destination.insert(position, node)
            for index, child in enumerate(destination):
                child["order"] = index
        else:
            raise Problem("INVALID_REQUEST", 422)
    return amended


def _siblings(placements, parent, *, without):
    return sorted(
        (
            row
            for row in placements.values()
            if row is not without and row.get("parentOccurrenceId") == parent
        ),
        key=lambda row: row["order"],
    )


def _valid_edit(data):
    if set(data) == {"operations"}:
        actions = data["operations"]
        return isinstance(actions, list) and 1 <= len(actions) <= 32
    return (
        set(data) == {"editableTopology"}
        and isinstance(data["editableTopology"], dict)
        and set(data["editableTopology"]) == {"resources", "occurrences"}
    )


def access(user, candidate_id, *, data=None, expected=None, key=None, resources=None):
    candidate = (
        Candidate.objects.select_related("selection")
        .filter(pk=candidate_id, selection__actor_id=user.pk)
        .first()
    )
    if candidate is None:
        raise Problem("NOT_FOUND", 404)
    with (
        handoff.scope(candidate.selection.workspace_id, resources),
        transaction.atomic(),
    ):
        candidate = (
            Candidate.objects.select_for_update(of=("self",))
            .select_related("selection")
            .filter(
                pk=candidate_id,
                selection__actor_id=user.pk,
                selection__workspace_id=candidate.selection.workspace_id,
            )
            .first()
        )
        if candidate is None:
            raise Problem("NOT_FOUND", 404)
        selection.current(user, candidate.selection, hydrate=False)
        if data is None:
            return serialize(candidate)
        if not _valid_edit(data):
            raise Problem("INVALID_REQUEST", 422)
        scope = "candidate-edit:" + candidate_id
        existing, intent = intents.check(user.pk, scope, key, [expected, data])
        if existing:
            return existing.acknowledgement
        if candidate.revision != expected:
            raise Problem("REVISION_CONFLICT", 409, revision=candidate.revision)
        topology = (
            _apply_operations(candidate.topology, data["operations"])
            if "operations" in data
            else data["editableTopology"]
        )
        validate_v2({**topology, "instructionText": "", "tasks": []})
        candidate.topology = topology
        candidate.revision += 1
        candidate.save(update_fields=["topology", "revision"])
        answer = serialize(candidate)
        intents.remember(user.pk, scope, key, intent, candidate, answer)
        return answer
