"""Bounded iterative forest validation shared by card definition and sorting."""

from researchloop.interaction.errors import Problem

CARD_LIMIT = 100
GROUP_LIMIT = 100
DEPTH_LIMIT = 5


def validate_forest(groups):
    if not isinstance(groups, list) or len(groups) > GROUP_LIMIT:
        raise Problem("LIMIT_EXCEEDED")
    nodes = {}
    ranks = set()
    for group in groups:
        identifier = group["groupId"]
        if identifier in nodes:
            raise Problem("DUPLICATE_ID")
        nodes[identifier] = group
        rank = (group.get("parentGroupId"), group["order"])
        if rank in ranks:
            raise Problem("INVALID_ORDER")
        ranks.add(rank)
    for identifier in nodes:
        _validate_path(identifier, nodes)
    return nodes


def _validate_path(identifier, nodes):
    seen = set()
    cursor = identifier
    while cursor is not None:
        if cursor in seen:
            raise Problem("CYCLE_FORBIDDEN")
        if cursor not in nodes:
            raise Problem("INVALID_PARENT")
        seen.add(cursor)
        if len(seen) > DEPTH_LIMIT:
            raise Problem("DEPTH_EXCEEDED")
        cursor = nodes[cursor].get("parentGroupId")
