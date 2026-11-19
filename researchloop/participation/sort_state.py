"""Participation-owned ordered snapshot; frozen method rows remain untouched."""

import copy
import json

from researchloop.cards.labels import validate_label
from researchloop.cards.topology import validate_forest
from researchloop.interaction.errors import Problem


def ordered_snapshot(state, content):
    if not isinstance(state, dict):
        raise Problem("INVALID_SORT_STATE")
    value = copy.deepcopy(state)
    cards = {c["cardId"] for c in content["cards"]}
    if set(value.get("membership", {})) != cards:
        raise Problem("INVALID_SORT_STATE")
    if "placements" not in value:
        value["placements"] = []
        for card in sorted(content["cards"], key=lambda c: c["order"]):
            parent = value["membership"][card["cardId"]]
            rank = sum(p["groupId"] == parent for p in value["placements"])
            value["placements"].append(
                {"cardId": card["cardId"], "groupId": parent, "order": rank}
            )
    if "groupOrder" not in value:
        value["groupOrder"] = []
        for group in sorted(value["groups"], key=lambda g: g["order"]):
            parent = group.get("parentGroupId")
            rank = sum(
                g.get("parentGroupId") == parent
                for g in value["groups"]
                if any(r["groupId"] == g["groupId"] for r in value["groupOrder"])
            )
            value["groupOrder"].append({"groupId": group["groupId"], "order": rank})
    validate_snapshot(value, content)
    return value


def validate_snapshot(state, content):
    # Existing delivery ceiling2MiB minus maximum262144-byte definition request
    # and16384 bytes of bounded session/ID envelope. This is a transport error,
    # not a reduced grapheme/group/card product limit. Never truncate raw text.
    if (
        len(json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode())
        > 2097152 - 262144 - 16384
    ):
        raise Problem("STATE_TRANSPORT_LIMIT")
    groups = state["groups"]
    fixed = {g["groupId"]: g for g in content["fixedGroups"]}
    # Index once rather than rescanning the full forest for every immutable
    # fixed node. validate_forest below still rejects duplicate identities.
    nodes = {g["groupId"]: g for g in groups}
    ids = set(nodes)
    if ids & set(state["membership"]):
        raise Problem("DUPLICATE_ID")
    if content["mode"] == "closed" and ids != set(fixed):
        raise Problem("MODE_FORBIDDEN")
    for identifier, original in fixed.items():
        if nodes.get(identifier) != original:
            raise Problem("MODE_FORBIDDEN")
    for group in groups:
        if group["groupId"] not in fixed:
            validate_label(group["label"], participant=True)
            if group.get("owner") != "participant":
                raise Problem("MODE_FORBIDDEN")
    ranks = _ranks(state["groupOrder"], "groupId", ids)
    effective = [{**g, "order": ranks[g["groupId"]]} for g in groups]
    validate_forest(effective)
    _contiguous(effective, "parentGroupId")
    placements = state["placements"]
    _ranks(placements, "cardId", set(state["membership"]))
    for placement in placements:
        card, parent = placement["cardId"], placement["groupId"]
        if parent != state["membership"][card] or (
            parent is not None and parent not in ids
        ):
            raise Problem("INVALID_SORT_STATE")
    _contiguous(placements, "groupId")


def _ranks(rows, key, identifiers):
    if not isinstance(rows, list) or len(rows) != len(identifiers):
        raise Problem("INVALID_SORT_STATE")
    if {row[key] for row in rows} != identifiers:
        raise Problem("INVALID_SORT_STATE")
    if any(type(row["order"]) is not int or row["order"] < 0 for row in rows):
        raise Problem("INVALID_ORDER")
    return {row[key]: row["order"] for row in rows}


def _contiguous(rows, parent_key):
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row.get(parent_key), []).append(row["order"])
    for values in by_parent.values():
        ranks = sorted(values)
        if ranks != list(range(len(ranks))):
            raise Problem("INVALID_ORDER")


def complete(state, content):
    try:
        value = ordered_snapshot(state, content)
    except (Problem, KeyError, TypeError, ValueError):
        return False
    return complete_validated(value, content)


def complete_validated(value, content):
    """Owner-internal predicate for a snapshot already fully validated above.

    Untrusted callers must use complete(); the evidence projection first calls
    ordered_snapshot and can reuse that exact returned value without validating
    the same immutable input twice.
    """
    if any(parent is None for parent in value["membership"].values()):
        return False
    used = set(value["membership"].values())
    nodes = {g["groupId"]: g for g in value["groups"]}
    for identifier in list(used):
        cursor = nodes[identifier].get("parentGroupId")
        while cursor is not None:
            used.add(cursor)
            cursor = nodes[cursor].get("parentGroupId")
    fixed = {g["groupId"] for g in content["fixedGroups"]}
    return all(g["groupId"] in used or g["groupId"] in fixed for g in value["groups"])
