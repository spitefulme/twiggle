from researchloop.interaction.errors import Problem
from researchloop.interaction.thin_bounds import identifier as check_id
from researchloop.cards.labels import validate_label
from researchloop.cards.topology import CARD_LIMIT, GROUP_LIMIT, validate_forest


def validate(content):
    if (
        not isinstance(content, dict)
        or set(content) != {"mode", "instructionText", "cards", "fixedGroups"}
        or not isinstance(content["mode"], str)
        or content["mode"] not in {"open", "closed", "hybrid"}
    ):
        raise Problem("INVALID_DEFINITION")
    validate_label(content["instructionText"], limit=2000, empty=True)
    for key, identifier in [("cards", "cardId"), ("fixedGroups", "groupId")]:
        rows = content[key]
        limit = CARD_LIMIT if key == "cards" else GROUP_LIMIT
        minimum = 0 if key == "fixedGroups" and content["mode"] == "open" else 1
        if not isinstance(rows, list) or not minimum <= len(rows) <= limit:
            raise Problem("LIMIT_EXCEEDED")
        ids = []
        for row in rows:
            allowed = {identifier, "label", "order"} | (
                {"parentGroupId", "owner"} if key == "fixedGroups" else set()
            )
            if (
                not isinstance(row, dict)
                or set(row) - allowed
                or not {identifier, "label", "order"} <= set(row)
            ):
                raise Problem("INVALID_DEFINITION")
            validate_label(row["label"])
            check_id(row[identifier])
            if type(row["order"]) is not int or not 0 <= row["order"] < limit:
                raise Problem("INVALID_ORDER")
            if key == "fixedGroups" and row.get("owner", "fixed") != "fixed":
                raise Problem("INVALID_DEFINITION")
            if key == "fixedGroups" and row.get("parentGroupId") is not None:
                check_id(row["parentGroupId"])
            ids.append(row[identifier])
        if len(set(ids)) != len(ids):
            raise Problem("DUPLICATE_ID")
    if content["mode"] == "open" and content["fixedGroups"]:
        raise Problem("MODE_FORBIDDEN")
    if {r["cardId"] for r in content["cards"]} & {
        r["groupId"] for r in content["fixedGroups"]
    }:
        raise Problem("DUPLICATE_ID")
    if len({r["order"] for r in content["cards"]}) != len(content["cards"]):
        raise Problem("INVALID_ORDER")
    validate_forest(content["fixedGroups"])
    return content


def apply(state, content, operation):
    if operation["operationKind"] != "move-card":
        raise Problem("UNSUPPORTED_THIN_METHOD")
    payload = operation["payload"]
    if set(payload) != {"cardId", "targetGroupId"}:
        raise Problem("INVALID_OPERATION")
    card, group = payload["cardId"], payload["targetGroupId"]
    if card not in state["membership"] or (
        group is not None and group not in {g["groupId"] for g in state["groups"]}
    ):
        raise Problem("INVALID_OPERATION")
    state["membership"][card] = group
    return state


def complete(state, content):
    return set(state.get("membership", {})) == {
        c["cardId"] for c in content["cards"]
    } and all(
        g in {x["groupId"] for x in state["groups"]}
        for g in state["membership"].values()
    )
