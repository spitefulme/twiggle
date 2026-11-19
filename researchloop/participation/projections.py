import copy
from researchloop.participation.sort_state import ordered_snapshot
from researchloop.participation import tree_state


def initial_state(version):
    content = version.content
    if version.study.method == "tree-test-v2":
        return tree_state.initial(content)
    if version.study.method == "card-sort":
        return ordered_snapshot(
            {
                "membership": {c["cardId"]: None for c in content["cards"]},
                "groups": copy.deepcopy(content["fixedGroups"]),
            },
            content,
        )
    return {
        "tasks": [
            {
                "taskId": t["taskId"],
                "route": [],
                "current": None,
                "events": [],
                "startedAt": None,
                "terminalAt": None,
                "terminalCategory": "incomplete",
            }
            for t in content["tasks"]
        ]
    }


def participant_view(session):
    content = session.version.content
    if session.version.study.method == "tree-test-v2":
        return tree_state.projection(session.state, content)
    if session.version.study.method == "card-sort":
        from researchloop.participation.models import Operation

        state = ordered_snapshot(session.state, content)
        undo = Operation.objects.filter(
            session=session,
            resulting_revision=session.revision,
            inverse_data__isnull=False,
            undone_by__isnull=True,
        ).first()
        return {
            "mode": content["mode"],
            "instructionText": content["instructionText"],
            "cards": content["cards"],
            "groups": state["groups"],
            "unassignedCardIds": [
                p["cardId"]
                for p in sorted(state["placements"], key=lambda p: p["order"])
                if p["groupId"] is None
            ],
            "membership": state["membership"],
            "placements": state["placements"],
            "groupOrder": state["groupOrder"],
            "undoOperationId": undo.operation_id if undo else None,
        }
    task = next(
        (t for t in session.state["tasks"] if t["terminalCategory"] == "incomplete"),
        None,
    )
    definition = next(
        (t for t in content["tasks"] if task and t["taskId"] == task["taskId"]), None
    )
    current = task["current"] if task else None
    labels = {r["resourceId"]: r["label"] for r in content["resources"]}
    nodes = [
        {**o, "label": labels[o["resourceId"]]}
        for o in content["occurrences"]
        if o.get("parentOccurrenceId") == current
    ]
    return {
        "instructionText": content["instructionText"],
        "currentTask": {"taskId": definition["taskId"], "prompt": definition["prompt"]}
        if definition
        else None,
        "rootOccurrences": nodes,
        "workingState": session.state,
    }


def session_data(session):
    return {
        "sessionId": str(session.pk),
        "versionId": str(session.version_id),
        "method": session.version.study.method,
        "revision": session.revision,
        "saveState": "saved",
        "status": session.status,
        "participantView": participant_view(session),
    }


def final_data(response):
    return {
        "responseId": str(response.pk),
        "versionId": str(response.version_id),
        "terminalState": "submitted",
    }
