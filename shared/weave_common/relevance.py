"""Dependency-light lexical ranking shared by context-source runtimes."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

KEY_LENGTH = 5
_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a an the and or but if to of for with about from into on in at by as is are was were be
    been being that this those these it its his her their our your my me i you he she they
    them we us do does did not no so just some any all can could should would will need needs
    regarding which because also uh um well get got up out then than there here when what who
    """.split()  # noqa: SIM905
)


def terms(text: str) -> list[str]:
    """Return informative whole words in source order, without duplicates.

    `keys` truncates to a fixed prefix so two spellings of a word compare equal.
    Search backends match whole words, so a truncated key ("docum") matches
    nothing at all there. Callers building a remote query want these instead.

    Source order is deliberate. Ordering by length looks like a proxy for
    specificity and is the opposite one here: the most discriminating terms in
    this domain are acronyms -- VDI, GCP, SRE -- so any caller capping the list
    would drop precisely them and search only generic words.
    """
    seen: list[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) > 2 and word not in STOPWORDS and word not in seen:
            seen.append(word)
    return seen


def keys(text: str) -> set[str]:
    """Return informative, prefix-normalized comparison keys for text."""
    return {
        word[:KEY_LENGTH]
        for word in _WORD.findall(text.lower())
        if len(word) > 2 and word not in STOPWORDS
    }


def rank(query: str, candidates: Sequence[str]) -> list[tuple[int, float]]:
    """Order candidates by IDF-weighted cosine similarity to the query."""
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
