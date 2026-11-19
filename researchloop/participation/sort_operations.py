"""Atomic intent transformations; persistence/replay belong to services."""

import copy
import hashlib
import json

from researchloop.cards.labels import validate_label
from researchloop.interaction.errors import Problem
from researchloop.interaction.thin_bounds import identifier
from researchloop.participation.sort_state import ordered_snapshot, validate_snapshot


def state_digest(state):
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def apply(state, content, operation, operation_id):
    result = ordered_snapshot(state, content)
    prior = copy.deepcopy(result)
    kind, payload = operation["operationKind"], operation["payload"]
    handlers = {
        "move-card": move_card,
        "move-group": move_group,
        "create-group": create_group,
        "create-subgroup": create_group,
        "rename-group": rename_group,
        "delete-group": delete_group,
    }
    if kind not in handlers:
        raise Problem("INVALID_OPERATION")
    handlers[kind](result, content, payload, operation_id)
    validate_snapshot(result, content)
    return result, {
        "priorState": prior,
        "resultDigest": state_digest(result),
        "operationKind": kind,
    }


def fields(payload, required, optional=()):
    if set(payload) - set(required) - set(optional) or not set(required) <= set(
        payload
    ):
        raise Problem("INVALID_OPERATION")
    for key, value in payload.items():
        if key.endswith("Id") and value is not None and not isinstance(value, str):
            raise Problem("INVALID_OPERATION")


def group(state, group_id):
    return next((g for g in state["groups"] if g["groupId"] == group_id), None)


def owned(state, content, group_id):
    node = group(state, group_id)
    if node is None:
        raise Problem("INVALID_OPERATION")
    if content["mode"] == "closed" or group_id in {
        g["groupId"] for g in content["fixedGroups"]
    }:
        raise Problem("MODE_FORBIDDEN")
    return node


def parent_allowed(state, content, parent):
    if parent is None:
        return
    node = group(state, parent)
    if node is None:
        raise Problem("INVALID_PARENT")
    if content["mode"] == "open" and node.get("owner") != "participant":
        raise Problem("MODE_FORBIDDEN")


def place_card(state, card_id, parent, index=None):
    if card_id not in state["membership"]:
        raise Problem("INVALID_OPERATION")
    old = state["membership"][card_id]
    target = sorted(
        (
            p
            for p in state["placements"]
            if p["groupId"] == parent and p["cardId"] != card_id
        ),
        key=lambda p: p["order"],
    )
    index = len(target) if index is None else index
    if type(index) is not int or not 0 <= index <= len(target):
        raise Problem("INVALID_ORDER")
    row = next(p for p in state["placements"] if p["cardId"] == card_id)
    row["groupId"] = parent
    target.insert(index, row)
    for rank, item in enumerate(target):
        item["order"] = rank
    if old != parent:
        for rank, item in enumerate(
            sorted(
                (p for p in state["placements"] if p["groupId"] == old),
                key=lambda p: p["order"],
            )
        ):
            item["order"] = rank
    state["membership"][card_id] = parent


def move_card(state, content, payload, operation_id):
    fields(payload, {"cardId", "targetGroupId"}, {"targetIndex"})
    parent_allowed(state, content, payload["targetGroupId"])
    place_card(
        state, payload["cardId"], payload["targetGroupId"], payload.get("targetIndex")
    )


def group_siblings(state, parent, exclude=None):
    ranks = {r["groupId"]: r["order"] for r in state["groupOrder"]}
    return sorted(
        (
            g
            for g in state["groups"]
            if g.get("parentGroupId") == parent and g["groupId"] != exclude
        ),
        key=lambda g: ranks[g["groupId"]],
    )


def set_group_order(state, siblings):
    ranks = {r["groupId"]: r for r in state["groupOrder"]}
    for rank, node in enumerate(siblings):
        ranks[node["groupId"]]["order"] = rank
        if node.get("owner") == "participant":
            node["order"] = rank


def place_group(state, node, parent, index=None):
    old = node.get("parentGroupId")
    siblings = group_siblings(state, parent, node["groupId"])
    index = len(siblings) if index is None else index
    if type(index) is not int or not 0 <= index <= len(siblings):
        raise Problem("INVALID_ORDER")
    node["parentGroupId"] = parent
    siblings.insert(index, node)
    set_group_order(state, siblings)
    if old != parent:
        set_group_order(state, group_siblings(state, old))


def move_group(state, content, payload, operation_id):
    fields(payload, {"groupId", "targetParentGroupId"}, {"targetIndex"})
    node = owned(state, content, payload["groupId"])
    parent_allowed(state, content, payload["targetParentGroupId"])
    place_group(state, node, payload["targetParentGroupId"], payload.get("targetIndex"))


def create_group(state, content, payload, operation_id):
    fields(payload, {"elementId", "label"}, {"parentGroupId", "targetIndex"})
    if content["mode"] == "closed":
        raise Problem("MODE_FORBIDDEN")
    if len(state["groups"]) >= 100:
        raise Problem("LIMIT_EXCEEDED")
    validate_label(payload["label"], participant=True)
    parent = payload.get("parentGroupId")
    parent_allowed(state, content, parent)
    element = payload["elementId"]
    source = None if element in state["membership"] else owned(state, content, element)
    group_id = "p" + hashlib.sha256(operation_id.encode()).hexdigest()[:22]
    identifier(group_id)
    if group(state, group_id) or group_id in state["membership"]:
        raise Problem("DUPLICATE_ID")
    node = {
        "groupId": group_id,
        "label": payload["label"],
        "owner": "participant",
        "parentGroupId": parent,
        "order": len(group_siblings(state, parent)),
    }
    state["groups"].append(node)
    state["groupOrder"].append({"groupId": group_id, "order": node["order"]})
    if source is None:
        place_card(state, element, group_id)
    else:
        place_group(state, source, group_id)
    place_group(state, node, parent, payload.get("targetIndex"))


def rename_group(state, content, payload, operation_id):
    fields(payload, {"groupId", "label"})
    node = owned(state, content, payload["groupId"])
    node["label"] = validate_label(payload["label"], participant=True)


def delete_group(state, content, payload, operation_id):
    fields(payload, {"groupId", "disposition"})
    node = owned(state, content, payload["groupId"])
    if payload["disposition"] == "cancel":
        raise Problem("CANCELLED_OPERATION")
    if not isinstance(payload["disposition"], str) or payload["disposition"] not in {
        "move-to-parent",
        "unassign",
    }:
        raise Problem("INVALID_OPERATION")
    parent = node.get("parentGroupId")
    for child in list(group_siblings(state, node["groupId"])):
        place_group(state, child, parent)
    for card, destination in list(state["membership"].items()):
        if destination == node["groupId"]:
            place_card(
                state,
                card,
                parent if payload["disposition"] == "move-to-parent" else None,
            )
    state["groups"].remove(node)
    state["groupOrder"] = [
        r for r in state["groupOrder"] if r["groupId"] != node["groupId"]
    ]
    set_group_order(state, group_siblings(state, parent))
