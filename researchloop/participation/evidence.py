"""Owned coherent evidence projection; consumers do not read participation tables."""

import copy
import hashlib
import json
from django.db.models import Case, F, FilteredRelation, JSONField, Q, Subquery, When

from researchloop.participation.models import (
    EvidenceClock,
    Operation,
    ParticipantSession,
    Response,
)
from researchloop.participation.sort_state import complete as card_complete
from researchloop.participation.sort_state import ordered_snapshot
from researchloop.participation.sort_state import complete_validated
from researchloop.interaction.errors import Problem
from researchloop.trees.services import complete as tree_complete


def tree_analysis_snapshot(version_id, suppressed, *, attribution=False):
    """Pin session state and evidence stamp in one PostgreSQL MVCC statement.

    The append-only journal can be fetched separately: retain only rows at
    or before each session revision in the snapshot. Acknowledgements,
    revision and journal append commit atomically. Suppression/cleanup is
    excluded by the caller's shared delivery gate.
    """
    clock = EvidenceClock.objects.filter(version_id=version_id).values("revision")[:1]
    rows = list(
        ParticipantSession.objects.filter(version_id=version_id)
        .exclude(pk__in=suppressed)
        .annotate(evidence_stamp=Subquery(clock))
        .order_by("pk")
        .values_list("pk", "state", "revision", "evidence_stamp")
    )
    sessions = {identity: (state, revision) for identity, state, revision, _ in rows}
    stamp = rows[0][3] if rows else clock.first()
    if isinstance(stamp, dict):
        stamp = stamp["revision"]
    attempted = {
        identity: [[] for _ in state["tasks"]]
        for identity, (state, _) in sessions.items()
    }
    task_cursor, seen = {}, {}
    missing = set()
    rows = (
        Operation.objects.filter(session_id__in=sessions)
        .order_by("session_id", "sequence")
        .values_list("session_id", "sequence", "payload")
    )
    for identity, sequence, data in rows.iterator(chunk_size=2000):
        if sequence > sessions[identity][1]:
            continue
        cursor = task_cursor.get(identity, 0)
        if sequence != seen.get(identity, 0) + 1 or cursor >= len(attempted[identity]):
            missing.add(identity)
        seen[identity] = sequence
        if cursor >= len(attempted[identity]):
            continue
        kind = data["operationKind"]
        attempted[identity][cursor].append(
            {"kind": kind, "occurrenceId": data["payload"].get("occurrenceId")}
        )
        if kind in {"destination-selected", "give-up"}:
            task_cursor[identity] = cursor + 1
    attempts = []
    attribution_by_attempt = {}
    for identity, (state, revision) in sessions.items():
        gap = identity in missing or seen.get(identity, 0) != revision
        attempts.extend(
            ({**attempt, "gap": gap}, events)
            for attempt, events in zip(state["tasks"], attempted[identity], strict=True)
        )
        if attribution:
            for attempt in state["tasks"]:
                attempt_id = attempt["attemptId"]
                if attempt_id in attribution_by_attempt:
                    raise Problem("COHORT_STALE", 409)
                attribution_by_attempt[attempt_id] = str(identity)
    if attribution:
        return attempts, attribution_by_attempt, stamp or 0
    return attempts, list(map(str, sessions)), stamp or 0


def analysis_snapshot(version_id, definition, suppressed):
    """One typed live-session projection; previews live in a different owner table."""
    # Fetch only the one authoritative state for each session. Submitted
    # session and Response JSON can both be large; a scoped left join with an
    # identity-based CASE avoids fetching/decoding both. JSON null from an
    # existing response is still malformed terminal evidence, never a fallback.
    rows = (
        ParticipantSession.objects.filter(version_id=version_id)
        .exclude(pk__in=suppressed)
        .annotate(
            owned_response=FilteredRelation(
                "response", condition=Q(response__version_id=version_id)
            )
        )
        .annotate(
            selected_state=Case(
                When(owned_response__pk__isnull=False, then=F("owned_response__state")),
                default=F("state"),
                output_field=JSONField(),
            ),
            selected_revision=Case(
                When(
                    owned_response__pk__isnull=False, then=F("owned_response__revision")
                ),
                default=F("revision"),
            ),
        )
        .order_by("pk")
        .values_list("pk", "owned_response__pk", "selected_revision", "selected_state")
        .iterator(chunk_size=100)
    )
    records = []
    for subject_id, response_id, revision, state in rows:
        # ORM JSON values are freshly materialized for this coherent read. Keep
        # that lossless raw value; separate projected fields are copied below.
        submitted = response_id is not None
        status = "submitted" if submitted else "partial" if revision else "started"
        structural = "valid" if submitted else "partial"
        try:
            validated = ordered_snapshot(state, definition)
            if submitted and not complete_validated(validated, definition):
                raise ValueError("incomplete terminal evidence")
        except (Problem, KeyError, TypeError, ValueError):
            structural = "malformed"
        records.append(
            {
                "responseId": str(response_id) if submitted else None,
                "evidenceId": str(response_id if submitted else subject_id),
                "subjectReference": str(subject_id),
                "status": status,
                "structuralStatus": structural,
                "preview": False,
                "stateRevision": revision,
                "rawDigest": hashlib.sha256(
                    json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "groups": copy.deepcopy(state["groups"])
                if isinstance(state, dict) and isinstance(state.get("groups"), list)
                else [],
                "membership": copy.deepcopy(state["membership"])
                if isinstance(state, dict) and isinstance(state.get("membership"), dict)
                else {},
                "rawState": state,
            }
        )
    clock = EvidenceClock.objects.filter(version_id=version_id).first()
    return {"records": records, "evidenceRevision": clock.revision if clock else 0}


def minimal_snapshot(version, suppressed):
    definition = version.content
    sessions = list(
        ParticipantSession.objects.filter(version=version)
        .exclude(pk__in=suppressed)
        .order_by("started_at")
    )
    responses = {r.session_id: r for r in Response.objects.filter(version=version)}
    counts = {"started": len(sessions), "partial": 0, "submitted": 0, "invalid": 0}
    rows = []
    for session in sessions:
        submitted = session.pk in responses
        status = "submitted" if submitted else "partial"
        if submitted:
            counts["submitted"] += 1
        elif session.revision:
            counts["partial"] += 1
        if not session.revision and not submitted:
            continue
        state = responses[session.pk].state if submitted else session.state
        row = {
            "evidenceId": str(responses[session.pk].pk)
            if submitted
            else str(session.pk),
            "stateRevision": session.revision,
            "status": status,
            "method": version.study.method,
        }
        try:
            if version.study.method == "card-sort":
                known = {c["cardId"] for c in definition["cards"]}
                if set(state["membership"]) - known:
                    raise ValueError("unknown card")
                row["memberships"] = dict(state["membership"])
                row["ownedGroups"] = [
                    {
                        "groupId": g["groupId"],
                        "originalLabel": g["label"],
                        "parentGroupId": g.get("parentGroupId"),
                        "order": g["order"],
                    }
                    for g in state["groups"]
                ]
                valid = card_complete(state, definition) if submitted else True
            else:
                row["tasks"] = [
                    {
                        "taskId": t["taskId"],
                        "route": t["route"],
                        "terminalCategory": t["terminalCategory"],
                    }
                    for t in state["tasks"]
                ]
                valid = tree_complete(state, definition) if submitted else True
            if not valid:
                raise ValueError("incomplete terminal evidence")
        except (KeyError, TypeError, ValueError):
            counts["invalid"] += 1
            row["status"] = "invalid"
            if version.study.method == "card-sort":
                row.setdefault("memberships", {})
                row.setdefault("ownedGroups", [])
            else:
                row.setdefault("tasks", [])
        rows.append(row)
    clock = EvidenceClock.objects.filter(version=version).first()
    return counts, rows, clock.revision if clock else 0
