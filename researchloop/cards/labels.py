"""Pinned Unicode17 validation; accepted raw text is never normalized."""

import regex
import unicodedata2 as unicode

from researchloop.interaction.errors import Problem

GRAPHEMES = regex.compile(r"\X", regex.VERSION1)
WHITESPACE = regex.compile(r"\p{White_Space}+", regex.VERSION1)
SAFE_FORMAT = {"\u200c", "\u200d"}


def validate_label(value, *, participant=False, limit=120, empty=False):
    if not isinstance(value, str):
        raise Problem("INVALID_LABEL")
    # Transport is independently bounded by the existing request envelope.
    # No code-point/byte limit silently replaces the approved grapheme limit.
    if any(
        unicode.category(c) in {"Cc", "Cs", "Zl", "Zp"}
        or (unicode.category(c) == "Cf" and c not in SAFE_FORMAT)
        for c in value
    ):
        raise Problem("UNSAFE_TEXT")
    normalized = unicode.normalize("NFC", value)
    tokens = [token for token in WHITESPACE.split(normalized) if token]
    if not tokens and not empty:
        raise Problem("LABEL_REQUIRED")
    if len(GRAPHEMES.findall(value)) > limit:
        raise Problem("LABEL_LENGTH_LIMIT")
    if participant and not 1 <= len(tokens) <= 7:
        raise Problem("LABEL_TOKEN_LIMIT")
    return value
