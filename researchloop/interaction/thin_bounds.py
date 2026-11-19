"""Declared U1-only range; full U2/U3 release envelopes are unchanged."""

import re
import unicodedata
from researchloop.interaction.errors import Problem

CARD_LIMIT = 8
GROUP_LIMIT = 2
TREE_NODE_LIMIT = 16
TREE_DEPTH_LIMIT = 4
TREE_TASK_LIMIT = 1
TREE_ROUTE_LIMIT = 32


def identifier(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value) is None
    ):
        raise Problem("UNSUPPORTED_THIN_IDENTIFIER")
    return value


def text(value, limit, empty=False):
    if (
        not isinstance(value, str)
        or (not value and not empty)
        or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)
        or len(value.encode("utf-8")) > limit
    ):
        raise Problem("INVALID_LABEL")
    return value
