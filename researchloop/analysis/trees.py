"""Exact tree scoring over an immutable, acknowledged evidence cohort."""

from collections import Counter
from math import ceil
from datetime import timedelta

from django.utils.dateparse import parse_datetime


def _elapsed(attempt):
    try:
        start = parse_datetime(attempt["startedAt"])
        terminal = parse_datetime(attempt["terminalAt"])
        milliseconds = (terminal - start) // timedelta(milliseconds=1)
        return milliseconds if milliseconds >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _timing(intervals, anomalies):
    intervals.sort()
    size = len(intervals)
    if not size:
        return {
            "medianMs": None,
            "iqrMs": None,
            "sampleSize": 0,
            "anomalies": anomalies,
        }

    def rank(fraction):
        return intervals[ceil(size * fraction) - 1]

    return {
        "medianMs": rank(0.5),
        "iqrMs": rank(0.75) - rank(0.25),
        "sampleSize": size,
        "anomalies": anomalies,
    }


def _rates(counts):
    values = {}
    for numerator, denominator in ("SE", "RE", "DE", "IE", "UE", "SF", "RF", "FE"):
        n, d = counts[numerator], counts[denominator]
        values[numerator + "/" + denominator] = {
            "numerator": n,
            "denominator": d,
            "value": n / d if d else None,
        }
    return values


def _directness(events, attempt, nodes):
    if any(event["kind"] == "ancestor-return" for event in events):
        return "indirect"
    if (
        attempt.get("gap")
        or not events
        or events[0]["kind"] != "task-start"
        or events[-1]["kind"] != "destination-selected"
        or events[-1]["occurrenceId"] != attempt["destination"]
    ):
        return "unknown"
    current = None
    for event in events[1:-1]:
        occurrence = event["occurrenceId"]
        if (
            event["kind"] != "child-entry"
            or occurrence not in nodes
            or nodes[occurrence].get("parentOccurrenceId") != current
        ):
            return "unknown"
        current = occurrence
    return "direct" if current == attempt["destination"] else "unknown"


def score_tree_v2(definition, attempts, checkpoint=lambda: None):
    nodes = {node["occurrenceId"]: node for node in definition["occurrences"]}
    correct = {
        task["taskId"]: (
            set(task["acceptedResourceIds"]),
            set(task["acceptedOccurrenceIds"]),
        )
        for task in definition["tasks"]
    }
    counts = dict.fromkeys("EFSRDIU", 0)
    first, destinations, routes = Counter(), Counter(), Counter()
    intervals, give_up_intervals, projected = [], [], []
    anomalies = give_up_anomalies = never_started = 0
    for number, (attempt, events) in enumerate(attempts):
        if number % 256 == 0:
            checkpoint()
        if not attempt.get("startedAt"):
            never_started += 1
            continue
        counts["E"] += 1
        terminal = attempt["terminalCategory"]
        destination = attempt.get("destination")
        acknowledged = [
            (event["kind"], event["occurrenceId"])
            for event in events
            if event["kind"] in {"child-entry", "ancestor-return"}
        ]
        first[
            next(
                (
                    occurrence
                    for kind, occurrence in acknowledged
                    if kind == "child-entry"
                ),
                "no-forward-choice",
            )
        ] += 1
        route = tuple(
            occurrence if occurrence is not None else ""
            for _, occurrence in acknowledged
        )
        routes[route] += 1
        directness = "unknown"
        if terminal in {"selected", "give-up"}:
            counts["F"] += 1
            destinations[destination if terminal == "selected" else "give-up"] += 1
            elapsed = _elapsed(attempt)
            if elapsed is None:
                if terminal == "give-up":
                    give_up_anomalies += 1
                else:
                    anomalies += 1
            else:
                if terminal == "give-up":
                    give_up_intervals.append(elapsed)
                else:
                    intervals.append(elapsed)
        if terminal == "selected":
            directness = _directness(events, attempt, nodes)
            resources, restricted = correct[attempt["taskId"]]
            reached = nodes.get(destination, {}).get("resourceId") in resources
            counts["R"] += reached
            if reached and (not restricted or destination in restricted):
                counts["S"] += 1
                counts[directness[0].upper()] += 1
        projected.append(
            {
                "attemptId": attempt["attemptId"],
                "taskId": attempt["taskId"],
                "route": list(route),
                "routeEvents": [
                    {"kind": kind, "occurrenceId": occurrence}
                    for kind, occurrence in acknowledged
                ],
                "historyGap": bool(attempt.get("gap")),
                "terminalCategory": terminal,
                "directness": directness,
            }
        )
    return {
        "counts": counts,
        "rates": _rates(counts),
        "neverStarted": never_started,
        "firstChoice": [
            {"category": label, "count": n, "denominatorE": counts["E"]}
            for label, n in sorted(first.items())
        ],
        "destinations": [
            {"category": label, "count": n, "denominatorF": counts["F"]}
            for label, n in sorted(destinations.items())
        ],
        "routes": [
            {"route": list(route), "count": n} for route, n in sorted(routes.items())
        ],
        "timing": _timing(intervals, anomalies),
        "giveUpTiming": _timing(give_up_intervals, give_up_anomalies),
        "attempts": projected,
    }
