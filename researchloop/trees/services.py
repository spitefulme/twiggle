from researchloop.interaction.errors import Problem
from researchloop.cards.labels import validate_label
from researchloop.interaction.thin_bounds import (
    identifier,
    text,
    TREE_NODE_LIMIT,
    TREE_DEPTH_LIMIT,
    TREE_TASK_LIMIT,
    TREE_ROUTE_LIMIT,
)


def _v2_rows(content, key, identifier_key, limit):
    rows = content[key]
    if not isinstance(rows, list):
        raise Problem("INVALID_DEFINITION")
    if len(rows) > limit:
        raise Problem("LIMIT_EXCEEDED")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise Problem("INVALID_DEFINITION")
        key_value = identifier(row.get(identifier_key))
        if key_value in indexed:
            raise Problem("DUPLICATE_ID")
        indexed[key_value] = row
    return indexed


def _v2_topology(nodes, resources):
    sibling_orders = set()
    for node in nodes.values():
        allowed = {
            "kind",
            "occurrenceId",
            "parentOccurrenceId",
            "order",
            "displayLabel",
        }
        kind = node.get("kind")
        if kind == "heading":
            validate_label(node.get("displayLabel"))
        elif kind == "resource":
            allowed.add("resourceId")
            if identifier(node.get("resourceId")) not in resources:
                raise Problem("INVALID_TOPOLOGY")
            if "displayLabel" in node:
                validate_label(node["displayLabel"])
        else:
            raise Problem("INVALID_TOPOLOGY")
        if set(node) - allowed:
            raise Problem("INVALID_TOPOLOGY")
        parent = node.get("parentOccurrenceId")
        if parent is not None and (
            identifier(parent) not in nodes or parent == node["occurrenceId"]
        ):
            raise Problem("INVALID_TOPOLOGY")
        order = node.get("order")
        if (
            type(order) is not int
            or not 0 <= order < 1000
            or (parent, order) in sibling_orders
        ):
            raise Problem("INVALID_ORDER")
        sibling_orders.add((parent, order))
    for node_id in nodes:
        seen = set()
        current = node_id
        while current is not None:
            if current in seen or len(seen) >= 8:
                raise Problem("INVALID_TOPOLOGY")
            seen.add(current)
            current = nodes[current].get("parentOccurrenceId")


def _target_ids(task, field, limit):
    values = task.get(field)
    if not isinstance(values, list) or len(values) > limit:
        raise Problem("INVALID_TARGET")
    ids = {identifier(value) for value in values}
    if len(ids) != len(values):
        raise Problem("DUPLICATE_ID")
    return ids


def _in_field(path, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Problem as error:
        error.field_errors = [
            {
                "path": path,
                "code": error.code,
                "message": error.code.replace("_", " ").capitalize(),
            }
        ]
        raise


def _v2_task(task, resources, nodes, placed, publishing):
    if set(task) != {
        "taskId",
        "prompt",
        "acceptedResourceIds",
        "acceptedOccurrenceIds",
    }:
        raise Problem("INVALID_TASK")
    validate_label(task["prompt"], limit=2000)
    correct = _target_ids(task, "acceptedResourceIds", 20)
    restricted = _target_ids(task, "acceptedOccurrenceIds", 100)
    if (
        correct - resources.keys()
        or restricted - nodes.keys()
        or any(nodes[value].get("resourceId") not in correct for value in restricted)
        or (publishing and (not correct or not correct <= placed))
    ):
        raise Problem("INVALID_TARGET")


def validate_v2(content, *, publishing=False):
    if not isinstance(content, dict) or set(content) != {
        "instructionText",
        "resources",
        "occurrences",
        "tasks",
    }:
        raise Problem("INVALID_DEFINITION")
    _in_field(
        "/instructionText",
        validate_label,
        content["instructionText"],
        limit=2000,
        empty=True,
    )
    resources = _in_field(
        "/resources", _v2_rows, content, "resources", "resourceId", 500
    )
    nodes = _in_field(
        "/occurrences", _v2_rows, content, "occurrences", "occurrenceId", 1000
    )
    tasks = _in_field("/tasks", _v2_rows, content, "tasks", "taskId", 20)
    for index, resource in enumerate(resources.values()):
        if set(resource) != {"resourceId", "label"}:
            raise Problem(
                "INVALID_DEFINITION",
                field_errors=[
                    {
                        "path": f"/resources/{index}",
                        "code": "INVALID_DEFINITION",
                        "message": "Invalid resource definition",
                    }
                ],
            )
        _in_field(f"/resources/{index}/label", validate_label, resource["label"])
    _in_field("/occurrences", _v2_topology, nodes, resources)
    placed = {node.get("resourceId") for node in nodes.values()}
    if publishing and not tasks:
        raise Problem(
            "INVALID_TASK",
            field_errors=[
                {
                    "path": "/tasks",
                    "code": "INVALID_TASK",
                    "message": "Add a valid task before publication",
                }
            ],
        )
    for index, task in enumerate(tasks.values()):
        _in_field(
            f"/tasks/{index}", _v2_task, task, resources, nodes, placed, publishing
        )
    return content


def patch_v2(before, changes):
    """Apply one bounded author intent without retransmitting the entire tree."""
    identifiers = {
        "resources": "resourceId",
        "occurrences": "occurrenceId",
        "tasks": "taskId",
    }
    if not isinstance(changes, dict) or set(changes) - {
        "instructionText",
        *identifiers,
    }:
        raise Problem("INVALID_DEFINITION")
    result = {
        "instructionText": changes.get(
            "instructionText", before.get("instructionText", "")
        )
    }
    for field, id_field in identifiers.items():
        delta = changes.get(field)
        prior = list(before.get(field, []))
        if delta is None:
            result[field] = prior
            continue
        if not isinstance(delta, dict) or set(delta) - {"upsert", "remove", "order"}:
            raise Problem("INVALID_DEFINITION")
        upsert, remove = delta.get("upsert", []), delta.get("remove", [])
        sizes = {"resources": 500, "occurrences": 1000, "tasks": 20}
        if not isinstance(remove, list) or len(remove) > sizes[field]:
            raise Problem("LIMIT_EXCEEDED")
        removals = {identifier(value) for value in remove}
        updated = _v2_rows({field: upsert}, field, id_field, sizes[field])
        current = {row[id_field]: row for row in prior}
        if removals & updated.keys() or removals - current.keys():
            raise Problem("INVALID_DEFINITION")
        for key in removals:
            current.pop(key)
        current.update(updated)
        order = delta.get("order")
        if order is not None:
            if (
                field != "tasks"
                or not isinstance(order, list)
                or len(order) != len(current)
            ):
                raise Problem("INVALID_ORDER")
            order = [identifier(item) for item in order]
            if set(order) != current.keys():
                raise Problem("INVALID_ORDER")
        result[field] = (
            [current[key] for key in order]
            if order is not None
            else list(current.values())
        )
    return validate_v2(result)


def validate(content):
    if not isinstance(content, dict) or set(content) != {
        "instructionText",
        "resources",
        "occurrences",
        "tasks",
    }:
        raise Problem("INVALID_DEFINITION")
    text(content["instructionText"], 2000, empty=True)
    for key, limit, id_field in [
        ("resources", TREE_NODE_LIMIT, "resourceId"),
        ("occurrences", TREE_NODE_LIMIT, "occurrenceId"),
        ("tasks", TREE_TASK_LIMIT, "taskId"),
    ]:
        rows = content[key]
        if not isinstance(rows, list) or not 1 <= len(rows) <= limit:
            raise Problem("LIMIT_EXCEEDED")
        if any(not isinstance(r, dict) for r in rows):
            raise Problem("INVALID_DEFINITION")
        ids = [r.get(id_field) for r in rows]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(
            ids
        ):
            raise Problem("DUPLICATE_ID")
        from researchloop.interaction.thin_bounds import identifier as check_id

        for value in ids:
            check_id(value)
    resources = {r["resourceId"] for r in content["resources"]}
    nodes = {r["occurrenceId"]: r for r in content["occurrences"]}
    for r in content["resources"]:
        text(r.get("label"), 120)
        if (
            set(r) != {"resourceId", "label"}
            or not isinstance(r["label"], str)
            or not 1 <= len(r["label"]) <= 120
        ):
            raise Problem("INVALID_LABEL")
    for node in nodes.values():
        if not {"occurrenceId", "resourceId", "order"} <= set(node):
            raise Problem("INVALID_TOPOLOGY")
        identifier(node["resourceId"])
        if node.get("parentOccurrenceId") is not None:
            identifier(node["parentOccurrenceId"])
        if type(node["order"]) is not int or not 0 <= node["order"] < TREE_NODE_LIMIT:
            raise Problem("INVALID_ORDER")
        if (
            set(node) - {"occurrenceId", "resourceId", "parentOccurrenceId", "order"}
            or node["resourceId"] not in resources
        ):
            raise Problem("INVALID_TOPOLOGY")
        visited = set()
        current = node
        while current:
            if current["occurrenceId"] in visited or len(visited) >= TREE_DEPTH_LIMIT:
                raise Problem("INVALID_TOPOLOGY")
            visited.add(current["occurrenceId"])
            parent = current.get("parentOccurrenceId")
            if parent is not None and parent not in nodes:
                raise Problem("INVALID_TOPOLOGY")
            current = nodes.get(parent)
    for task in content["tasks"]:
        text(task.get("prompt"), 2000)
        for key, limit in [("acceptedResourceIds", 20), ("acceptedOccurrenceIds", 100)]:
            if not isinstance(task.get(key), list) or len(task[key]) > limit:
                raise Problem("INVALID_TARGET")
            for value in task[key]:
                identifier(value)
        if (
            set(task)
            != {"taskId", "prompt", "acceptedResourceIds", "acceptedOccurrenceIds"}
            or not task["prompt"]
            or len(task["prompt"]) > 2000
        ):
            raise Problem("INVALID_TASK")
        if (
            not task["acceptedResourceIds"]
            or set(task["acceptedResourceIds"]) - resources
            or set(task["acceptedOccurrenceIds"]) - set(nodes)
        ):
            raise Problem("INVALID_TARGET")
        placed = {node["resourceId"] for node in nodes.values()}
        if set(task["acceptedResourceIds"]) - placed or (
            task["acceptedResourceIds"]
            and any(
                nodes[value]["resourceId"] not in task["acceptedResourceIds"]
                for value in task["acceptedOccurrenceIds"]
            )
        ):
            raise Problem("INVALID_TARGET")
    return content


def apply(state, content, operation):
    from django.utils import timezone

    task = next(
        (t for t in state["tasks"] if t["terminalCategory"] == "incomplete"), None
    )
    if task is None:
        raise Problem("TERMINAL_TASK", 409)
    if not {"events", "route", "startedAt", "current"} <= set(task):
        raise Problem("INVALID_WORKING_STATE")
    if (
        len(task["events"]) >= 64
        and operation["operationKind"] not in {"destination-selected", "give-up"}
    ) or (
        len(task["route"]) >= TREE_ROUTE_LIMIT
        and operation["operationKind"] in {"child-entry", "ancestor-return"}
    ):
        raise Problem("UNSUPPORTED_THIN_ROUTE_LIMIT")
    kind, payload = operation["operationKind"], operation["payload"]
    if set(payload) - {"occurrenceId"}:
        raise Problem("INVALID_OPERATION")
    occurrence = payload.get("occurrenceId")
    nodes = {o["occurrenceId"]: o for o in content["occurrences"]}
    if kind == "task-start":
        if task["startedAt"] is not None or payload:
            raise Problem("INVALID_OPERATION")
        task["startedAt"] = timezone.now().isoformat()
    elif task["startedAt"] is None:
        raise Problem("TASK_NOT_STARTED")
    elif kind == "child-entry":
        if (
            occurrence not in nodes
            or nodes[occurrence].get("parentOccurrenceId") != task["current"]
        ):
            raise Problem("INVALID_OCCURRENCE")
        task["current"] = occurrence
        task["route"].append(occurrence)
    elif kind == "ancestor-return":
        current = nodes.get(task["current"])
        ancestors = []
        while current:
            ancestors.append(current.get("parentOccurrenceId"))
            current = nodes.get(current.get("parentOccurrenceId"))
        if occurrence not in ancestors:
            raise Problem("INVALID_OCCURRENCE")
        task["current"] = occurrence
        if occurrence is not None:
            task["route"].append(occurrence)
    elif kind in {"destination-selected", "give-up"}:
        if kind == "destination-selected" and (
            occurrence is None or occurrence != task["current"]
        ):
            raise Problem("INVALID_OCCURRENCE")
        if kind == "give-up" and payload:
            raise Problem("INVALID_OPERATION")
        task["terminalCategory"] = (
            "selected" if kind == "destination-selected" else "give-up"
        )
        task["terminalAt"] = timezone.now().isoformat()
    else:
        raise Problem("INVALID_OPERATION")
    task["events"].append(
        {
            "sequence": operation["sequence"],
            "eventKind": kind,
            "occurrenceId": occurrence,
            "at": timezone.now().isoformat(),
        }
    )
    return state


def complete(state, content):
    return len(state.get("tasks", [])) == len(content["tasks"]) and all(
        t["terminalCategory"] in {"selected", "give-up"} for t in state["tasks"]
    )
