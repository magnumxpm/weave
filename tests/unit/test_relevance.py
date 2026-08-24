from __future__ import annotations

from weave_common import rank, terms

from agent.context_sources.relevance import keys
from agent.context_sources.relevance import rank as legacy_rank

# The stored descriptions this ranking was measured against.
CORPUS = [
    "get back to me regarding the accounts team",
    "follow up with some emails",
    "write a document about your experience with VDI and GCP for the HR team",
    "send an email to me",
    "send me an email regarding that you've onboarded and you need your other devices",
]


def test_word_forms_share_a_key_in_both_directions() -> None:
    # Suffix stripping produced "docu" from "document" but "document" from
    # "documentation", so the one genuinely related pair never matched.
    assert keys("documentation") == keys("document")
    assert keys("devices") == keys("device")


def test_stopwords_and_short_words_carry_no_signal() -> None:
    assert keys("we need to get it up to me") == set()


def test_a_shared_topic_outranks_a_shared_common_verb() -> None:
    ranked = rank("Complete documentation work for the team", CORPUS)
    assert ranked, "a shared topic word must produce a candidate"
    best, _ = ranked[0]
    assert CORPUS[best].startswith("write a document")


def test_candidates_sharing_nothing_are_left_out() -> None:
    assert rank("Renew the parking permit", CORPUS) == []
    assert rank("", CORPUS) == []


def test_scores_are_ordered_and_within_the_contextmatch_range() -> None:
    ranked = rank("send an email about the devices", CORPUS)
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 < score <= 1.0 for score in scores)


def test_legacy_and_shared_import_paths_are_identical() -> None:
    assert legacy_rank("send device email", CORPUS) == rank("send device email", CORPUS)


def test_terms_keep_short_acronyms_that_a_length_ordering_would_bury() -> None:
    """Acronyms are the most discriminating terms here, and the shortest.

    An earlier version ordered terms by length before a caller capped the list,
    which dropped exactly `vdi` and `gcp` and searched only generic words.
    """
    extracted = terms(
        "Complete the documentation detailing your experience with VDI and GCP "
        "migration for the HR onboarding team review"
    )

    assert "vdi" in extracted[:12]
    assert "gcp" in extracted[:12]
    assert extracted[0] == "complete"  # source order, not length order
    assert "the" not in extracted and "your" not in extracted
