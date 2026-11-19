"""Pure, exact card-sort analysis of an already-authorized eligible cohort.

This module never reads owner tables or silently repairs malformed evidence.
Eligibility, suppression, annotations and delivery belong to the owning ports.
"""

import copy
from fractions import Fraction
from itertools import combinations

from researchloop.cards.labels import validate_label
from researchloop.interaction.errors import Problem

ALGORITHM = "average-linkage-original-pairs-v1"


def coded_view(records, assignments):
    """Copy annotation labels into a view; original identity/topology is intact."""
    result = copy.deepcopy(records)
    known_responses = {row["responseId"] for row in result}
    if set(assignments) - known_responses:
        raise Problem("INVALID_CODING")
    for row in result:
        annotations = assignments.get(row["responseId"], {})
        if not isinstance(annotations, dict):
            raise Problem("INVALID_CODING")
        groups = {group["groupId"]: group for group in row["groups"]}
        if set(annotations) - set(groups):
            raise Problem("INVALID_CODING")
        for group in row["groups"]:
            value = annotations.get(group["groupId"], group["label"])
            if group["groupId"] in annotations:
                validate_label(value, participant=False)
            group["displayLabel"] = value
            group["codingApplied"] = group["groupId"] in annotations
    return result


def select_cohort(records, exclusions):
    """Select one population from a coherent owner snapshot, never repair rows.

    Suppressed subjects must already be removed by the trusted owner port. The
    explicit preview marker is still checked so a preview cannot enter analysis
    if a caller accidentally mixes those typed projections.
    """
    counts = dict.fromkeys(
        ["started", "partial", "submitted", "invalid", "excluded", "included"], 0
    )
    included, reasons, submitted_ids = [], {}, set()
    previews = 0
    for row in records:
        if (
            type(row.get("preview")) is not bool
            or row.get("status") not in {"started", "partial", "submitted"}
            or row.get("structuralStatus") not in {"valid", "partial", "malformed"}
        ):
            raise Problem("INVALID_ANALYSIS_INPUT")
        if row["preview"]:
            previews += 1
            continue
        counts["started"] += 1
        counts["partial"] += row["status"] == "partial"
        counts["invalid"] += row["structuralStatus"] == "malformed"
        if row["status"] != "submitted":
            continue
        identifier = row.get("responseId")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in submitted_ids
        ):
            raise Problem("INVALID_ANALYSIS_INPUT")
        submitted_ids.add(identifier)
        counts["submitted"] += 1
        if row["structuralStatus"] != "valid":
            reasons[identifier] = "Malformed terminal evidence"
        elif identifier in exclusions:
            reasons[identifier] = exclusions[identifier]
        else:
            included.append(copy.deepcopy(row))
    if set(exclusions) - submitted_ids:
        raise Problem("INVALID_ANALYSIS_INPUT")
    counts["included"] = len(included)
    counts["excluded"] = counts["submitted"] - counts["included"]
    return {
        "counts": counts,
        "included": included,
        "exclusionReasons": reasons,
        "previewExcluded": previews,
        "populationExplanation": "Started counts nonpreview sessions. Partial excludes submitted. Invalid can overlap partial/submitted; excluded counts submitted evidence not included (malformed or researcher-excluded).",
    }


def fraction(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def average_linkage(card_ids, distances, check=lambda: None):
    """Mean original cross-card distances; stable tuple-pair ties, never jitter."""
    clusters = {
        tuple([identifier]): "card:" + identifier for identifier in sorted(card_ids)
    }
    merges = []
    while len(clusters) > 1:
        check()
        candidates = []
        for left, right in combinations(sorted(clusters), 2):
            check()
            total = sum((distances[(a, b)] for a in left for b in right), Fraction(0))
            candidates.append((total / (len(left) * len(right)), left, right))
        distance, left, right = min(candidates)
        members = tuple(sorted(left + right))
        merge_id = "merge:" + str(len(merges) + 1)
        merges.append(
            {
                "mergeId": merge_id,
                "leftId": clusters.pop(left),
                "rightId": clusters.pop(right),
                "left": list(left),
                "right": list(right),
                "members": list(members),
                "distance": fraction(distance),
            }
        )
        clusters[members] = merge_id
    return merges


def _paths(response, identifiers):
    groups = response["groups"]
    nodes = {group["groupId"]: group for group in groups}
    if len(nodes) != len(groups) or len(nodes) > 100:
        raise ValueError("duplicate or excessive groups")
    membership = response["membership"]
    if set(membership) != set(identifiers):
        raise ValueError("cohort membership differs from frozen cards")
    roots = {}
    for identifier in identifiers:
        parent = membership[identifier]
        path = set()
        if parent is None:
            raise ValueError("unassigned card in eligible cohort")
        while parent is not None:
            if parent not in nodes or parent in path or len(path) >= 5:
                raise ValueError("invalid group ancestry")
            path.add(parent)
            roots[identifier] = parent
            parent = nodes[parent].get("parentGroupId")
    return membership, roots


def _encode_partitions(identifiers, population, check):
    """Seven packed planes per partition: at most 100 groups in each response."""
    width = (len(population) + 7) // 8
    planes = {card: [bytearray(width) for _ in range(14)] for card in identifiers}
    for index, (membership, roots) in enumerate(population):
        check()
        # Codes are local to a response. Only equality at the same bit
        # position matters; group names and identities never cross responses.
        codes = {}
        for card in identifiers:
            for offset, group in ((0, membership[card]), (7, roots[card])):
                code = codes.setdefault(group, len(codes))
                bit = offset
                while code:
                    if code & 1:
                        planes[card][bit][index // 8] |= 1 << (index % 8)
                    code >>= 1
                    bit += 1
    return {
        card: tuple(int.from_bytes(plane, "little") for plane in encoded)
        for card, encoded in planes.items()
    }


def compute_matrices(card_ids, responses, check=lambda: None, metric="immediate"):
    if not isinstance(card_ids, (list, tuple)) or any(
        not isinstance(identifier, str) for identifier in card_ids
    ):
        raise Problem("INVALID_ANALYSIS_INPUT")
    identifiers = sorted(card_ids)
    if (
        metric not in {"immediate", "ancestor"}
        or len(identifiers) > 100
        or len(set(identifiers)) != len(identifiers)
        or any(not isinstance(identifier, str) for identifier in identifiers)
    ):
        raise Problem("INVALID_ANALYSIS_INPUT")
    population = []
    response_ids = []
    try:
        for response in responses:
            check()
            response_ids.append(response["responseId"])
            population.append(_paths(response, identifiers))
        if len(set(response_ids)) != len(response_ids):
            raise ValueError("duplicate response identity")
    except (KeyError, TypeError, ValueError) as error:
        raise Problem("INVALID_ANALYSIS_INPUT") from error
    denominator = len(population)
    partitions = _encode_partitions(identifiers, population, check)
    immediate = {identifier: {} for identifier in identifiers}
    ancestor = {identifier: {} for identifier in identifiers}
    for identifier in identifiers:
        check()
        immediate[identifier][identifier] = {
            "numerator": denominator,
            "denominator": denominator,
        }
        ancestor[identifier][identifier] = {
            "numerator": denominator,
            "denominator": denominator,
        }
    # In a validated forest two paths share an explicit ancestor exactly
    # when they share a root. XOR marks differing code bits per response;
    # OR across planes counts each differing response once.
    for left, right in combinations(identifiers, 2):
        check()
        differs = ancestor_differs = 0
        a, b = partitions[left], partitions[right]
        for bit in range(7):
            differs |= a[bit] ^ b[bit]
            ancestor_differs |= a[bit + 7] ^ b[bit + 7]
        same = denominator - differs.bit_count()
        shared = denominator - ancestor_differs.bit_count()
        for first, second in [(left, right), (right, left)]:
            immediate[first][second] = {"numerator": same, "denominator": denominator}
            ancestor[first][second] = {"numerator": shared, "denominator": denominator}
    selected = immediate if metric == "immediate" else ancestor
    distances = (
        {
            (left, right): 1 - Fraction(selected[left][right]["numerator"], denominator)
            for left in identifiers
            for right in identifiers
            if left != right
        }
        if denominator
        else {}
    )
    return {
        "availability": "ready" if denominator else "unavailable",
        "cardIds": identifiers,
        "cohortResponseIds": response_ids,
        "includedCount": denominator,
        "immediate": immediate,
        "ancestor": ancestor,
        "linkage": average_linkage(identifiers, distances, check)
        if denominator and len(identifiers) >= 2
        else [],
        "metric": metric,
        "algorithmVersion": ALGORITHM,
        "tieRule": "lexicographically-smallest-pair-of-sorted-stable-card-ID-tuples",
        "interpretation": "Linkage height is dissimilarity, not correctness or probability.",
    }


def contract_projection(result, cohort, counts, frozen_revision_id):
    """Additive C01/C04 wire projection of the same exact cohort and matrices."""
    keys = {"started", "partial", "submitted", "invalid", "excluded", "included"}
    if (
        set(counts) != keys
        or any(type(value) is not int or value < 0 for value in counts.values())
        or counts["included"] != result["includedCount"]
        or counts["submitted"] > counts["started"]
        or counts["included"] > counts["submitted"]
        or cohort["includedResponseIds"] != result["cohortResponseIds"]
    ):
        raise Problem("INVALID_ANALYSIS_INPUT")

    def cells(metric):
        return [
            {
                "leftCardId": left,
                "rightCardId": right,
                **result[metric][left][right],
                "value": result[metric][left][right]["numerator"]
                / result[metric][left][right]["denominator"]
                if result[metric][left][right]["denominator"]
                else None,
            }
            for left, right in combinations(result["cardIds"], 2)
        ]

    dendrogram = None
    if result["linkage"]:
        nodes = [
            {
                "nodeId": "card:" + identifier,
                "distance": 0,
                "exactDistance": {"numerator": 0, "denominator": 1},
                "memberCardIds": [identifier],
                "leftNodeId": None,
                "rightNodeId": None,
            }
            for identifier in result["cardIds"]
        ]
        nodes.extend(
            {
                "nodeId": merge["mergeId"],
                "distance": merge["distance"]["numerator"]
                / merge["distance"]["denominator"],
                "exactDistance": dict(merge["distance"]),
                "memberCardIds": list(merge["members"]),
                "leftNodeId": merge["leftId"],
                "rightNodeId": merge["rightId"],
            }
            for merge in result["linkage"]
        )
        dendrogram = {
            "method": "average-linkage",
            "tieRule": result["tieRule"],
            "includedResponseIds": list(result["cohortResponseIds"]),
            "nodes": nodes,
            "rootNodeId": nodes[-1]["nodeId"],
            "metric": result["metric"],
            "algorithmVersion": result["algorithmVersion"],
            "interpretation": result["interpretation"],
        }
    return {
        "method": "card-sort",
        "versionId": cohort["versionId"],
        "frozenRevisionId": str(frozen_revision_id),
        "cohort": copy.deepcopy(cohort),
        "counts": dict(counts),
        "immediateSimilarity": cells("immediate"),
        "ancestorSimilarity": cells("ancestor"),
        "matrixLayout": "symmetric-upper-triangle-without-diagonal",
        "cardIds": list(result["cardIds"]),
        "dendrogram": dendrogram,
        "analysisAvailability": result["availability"],
        "completion": {
            "numerator": counts["submitted"],
            "denominator": counts["started"],
            "value": counts["submitted"] / counts["started"]
            if counts["started"]
            else None,
            "label": "submitted-started-session-rate",
        },
    }
