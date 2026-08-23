"""Lexical ranking of context candidates against an action item.

Descriptions are one short sentence, so ranking has to survive plurals and
noun forms: "device"/"devices", "document"/"documentation". Truncating every
token to a fixed prefix is symmetric, which suffix stripping is not -- stripping
turned "document" into "docu" while "documentation" stopped at "document", so
the pair that actually related never matched.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

KEY_LENGTH = 5
_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    # A word list reads as prose; a list literal of eighty one-word strings does
    # not, which is the whole reason to keep the split here.
    """
    a an the and or but if to of for with about from into on in at by as is are was were be
    been being that this those these it its his her their our your my me i you he she they
    them we us do does did not no so just some any all can could should would will need needs
    regarding which because also uh um well get got up out then than there here when what who
    """.split()  # noqa: SIM905
)


def keys(text: str) -> set[str]:
    """Comparison keys for one description: informative words, prefix-truncated."""
    return {
        word[:KEY_LENGTH]
        for word in _WORD.findall(text.lower())
        if len(word) > 2 and word not in STOPWORDS
    }


def rank(query: str, candidates: Sequence[str]) -> list[tuple[int, float]]:
    """Order candidates by IDF-weighted cosine similarity to the query.

    Returns `(index, score)` best first, keeping only candidates that share at
    least one informative term. That `> 0` gate is deliberately not a tuned
    threshold: measured against real stored items, IDF over a window this small
    tracks candidate brevity as much as relevance -- a short "get back to me
    regarding the accounts team" outscored the one genuinely related item -- so
    any numeric floor would encode noise. Code narrows the field to candidates
    with a real lexical link and ranks them; deciding which of those actually
    relate is left to the model, which reads meaning rather than tokens.
    """
    query_keys = keys(query)
    if not query_keys:
        return []

    candidate_keys = [keys(text) for text in candidates]
    document_count = len(candidate_keys)
    frequency: dict[str, int] = {}
    for candidate in candidate_keys:
        for key in candidate:
            frequency[key] = frequency.get(key, 0) + 1

    def weight(key: str) -> float:
        return math.log(1 + document_count / (1 + frequency.get(key, 0)))

    query_norm = math.sqrt(sum(weight(key) ** 2 for key in query_keys))
    ranked: list[tuple[int, float]] = []
    for index, candidate in enumerate(candidate_keys):
        shared = query_keys & candidate
        if not shared:
            continue
        candidate_norm = math.sqrt(sum(weight(key) ** 2 for key in candidate))
        if not query_norm or not candidate_norm:
            continue
        score = sum(weight(key) ** 2 for key in shared) / (query_norm * candidate_norm)
        if score > 0:
            ranked.append((index, min(score, 1.0)))

    ranked.sort(key=lambda entry: (-entry[1], entry[0]))
    return ranked
