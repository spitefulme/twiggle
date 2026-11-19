"""Closed, server-authored traversal state for the versioned tree method."""

from uuid import uuid4

from django.utils import timezone

from researchloop.interaction.errors import Problem


def initial(content):
    return {
        "tasks": [
            {
                "taskId": task["taskId"],
                "attemptId": str(uuid4()),
                "current": None,
                "startedAt": None,
                "terminalAt": None,
                "terminalCategory": "incomplete",
                "destination": None,
            }
            for task in content["tasks"]
        ]
    }


def active_task(state):
    return next(
        (task for task in state["tasks"] if task["terminalCategory"] == "incomplete"),
        None,
    )


def apply(state, content, data):
    task = active_task(state)
    if task is None:
        raise Problem("TERMINAL_TASK", 409)
    kind, payload = data["operationKind"], data["payload"]
    now = timezone.now().isoformat()
    if kind == "task-start":
        if task["startedAt"] is not None or payload:
            raise Problem("INVALID_OPERATION")
        task["startedAt"] = now
        return state
    if task["startedAt"] is None:
        raise Problem("TASK_NOT_STARTED")
    if kind == "give-up":
        if payload:
            raise Problem("INVALID_OPERATION")
        task["terminalAt"], task["terminalCategory"] = now, "give-up"
        return state
    if set(payload) != {"occurrenceId"}:
        raise Problem("INVALID_OPERATION")
    occurrence = payload["occurrenceId"]
    if occurrence is not None and not isinstance(occurrence, str):
        raise Problem("INVALID_OCCURRENCE")
    nodes = {item["occurrenceId"]: item for item in content["occurrences"]}
    current = task["current"]
    if kind == "child-entry":
        if (
            occurrence not in nodes
            or nodes[occurrence].get("parentOccurrenceId") != current
        ):
            raise Problem("INVALID_OCCURRENCE")
        task["current"] = occurrence
    elif kind == "ancestor-return":
        ancestors = set()
        cursor = current
        while cursor is not None:
            cursor = nodes[cursor].get("parentOccurrenceId")
            ancestors.add(cursor)
        if occurrence not in ancestors:
            raise Problem("INVALID_OCCURRENCE")
        task["current"] = occurrence
    elif kind == "destination-selected":
        if (
            occurrence is None
            or occurrence != current
            or nodes[current]["kind"] != "resource"
        ):
            raise Problem("INVALID_OCCURRENCE")
        task["destination"] = occurrence
        task["terminalAt"], task["terminalCategory"] = now, "selected"
    else:
        raise Problem("INVALID_OPERATION")
    return state


def projection(state, content):
    active = active_task(state)
    nodes = {item["occurrenceId"]: item for item in content["occurrences"]}
    resources = {item["resourceId"]: item["label"] for item in content["resources"]}
    current = active["current"] if active else None

    def occurrence_view(node):
        label = node.get("displayLabel") or resources[node["resourceId"]]
        return {
            "occurrenceId": node["occurrenceId"],
            "label": label,
            "selectable": node["kind"] == "resource",
        }

    ancestors = []
    cursor = current
    while cursor is not None:
        ancestors.append(occurrence_view(nodes[cursor]))
        cursor = nodes[cursor].get("parentOccurrenceId")
    children = [
        occurrence_view(node)
        for node in sorted(nodes.values(), key=lambda item: item["order"])
        if node.get("parentOccurrenceId") == current and active is not None
    ]
    definition = next(
        (
            task
            for task in content["tasks"]
            if active and task["taskId"] == active["taskId"]
        ),
        None,
    )
    return {
        "instructionText": content["instructionText"],
        "activeTaskId": active["taskId"] if active else None,
        "prompt": definition["prompt"] if definition else None,
        "taskStarted": bool(active and active["startedAt"]),
        "currentOccurrenceId": current,
        "path": list(reversed(ancestors)),
        "children": children,
        "completedTasks": sum(
            task["terminalCategory"] != "incomplete" for task in state["tasks"]
        ),
        "taskCount": len(state["tasks"]),
    }
