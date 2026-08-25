from datetime import date
from typing import Any

from weave_common import (
    ActionItem,
    ActionType,
    CommitmentState,
    CommitmentStatus,
    EnrichedActionItem,
    MentionRelationship,
    ReconcileDecision,
)
from weave_ingestion.commitments import commitment_id_for, reconcile_meeting
from weave_ingestion.firestore_client import WrittenActionItem


def row(reference: str = "meeting--owner@example.com--0") -> WrittenActionItem:
    item = ActionItem(
        description="Ship the launch brief",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1,
        resolution_turn_ref=1,
    )
    return WrittenActionItem(
        mention_ref=reference,
        owner_email="owner@example.com",
        meeting_date=date(2026, 8, 25),
        enriched=EnrichedActionItem(item=item, title="Ship launch brief"),
        embedding=[0.1, 0.2],
    )


class Store:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates
        self.applied: list[tuple[ReconcileDecision, Any]] = []

    def candidates_for(self, owner: str, embedding: Any) -> list[dict[str, Any]]:
        assert owner == "owner@example.com"
        assert embedding == [0.1, 0.2]
        return self.candidates

    def apply(self, decision: ReconcileDecision, mention: Any, *args: Any, **kwargs: Any) -> str:
        self.applied.append((decision, mention))
        return decision.matched_commitment_id or commitment_id_for(mention.mention_ref)


def decision(match: str | None, confidence: float) -> ReconcileDecision:
    return ReconcileDecision(
        matched_commitment_id=match,
        confidence=confidence,
        relationship=MentionRelationship.CARRIED_OVER,
        canonical_title="Ship launch brief",
        inferred_state=CommitmentState.OPEN,
    )


def test_threshold_and_candidate_membership_are_enforced_in_python() -> None:
    store = Store([{"commitment_id": "known", "title": "Launch brief"}])
    reconcile_meeting(store, lambda text, candidates: decision("known", 0.79), [row()])
    assert store.applied[0][0].matched_commitment_id is None
    assert store.applied[0][0].relationship is MentionRelationship.ORIGINAL

    store = Store([{"commitment_id": "known", "title": "Launch brief"}])
    reconcile_meeting(store, lambda text, candidates: decision("hallucinated", 0.99), [row()])
    assert store.applied[0][0].matched_commitment_id is None


def test_model_failure_creates_original_instead_of_dropping_mention() -> None:
    store = Store([])

    def fail(text: str, candidates: list[dict[str, Any]]) -> ReconcileDecision:
        raise RuntimeError("model unavailable")

    result = reconcile_meeting(store, fail, [row()])
    applied = store.applied[0][0]
    assert applied.matched_commitment_id is None
    assert applied.relationship is MentionRelationship.ORIGINAL
    assert applied.inferred_state is CommitmentState.OPEN
    assert result == [commitment_id_for(row().mention_ref)]


def test_reconciler_never_turns_model_inference_into_closed() -> None:
    store = Store([])
    close = decision(None, 0.95).model_copy(update={"inferred_state": CommitmentState.CLOSED})
    reconcile_meeting(store, lambda text, candidates: close, [row()])
    assert store.applied[0][0].inferred_state is CommitmentState.OPEN

    store = Store([{"commitment_id": "known", "title": "Launch brief"}])
    matched_close = decision("known", 0.95).model_copy(
        update={"inferred_state": CommitmentState.CLOSED}
    )
    reconcile_meeting(store, lambda text, candidates: matched_close, [row()])
    assert store.applied[0][0].inferred_state is CommitmentState.LIKELY_COMPLETE


class BlockerStore:
    """Answers the mention lookup and the blocking-hint lookup separately."""

    def __init__(self, hint_candidates: list[dict[str, Any]]) -> None:
        self.hint_candidates = hint_candidates
        self.applied: list[dict[str, Any]] = []

    def candidates_for(self, owner: str, embedding: Any) -> list[dict[str, Any]]:
        # The mention itself is new; only the hint lookup has candidates.
        return self.hint_candidates if embedding == [9.0] else []

    def apply(self, decision: ReconcileDecision, mention: Any, *args: Any, **kwargs: Any) -> str:
        self.applied.append({"decision": decision, "blocked_by": kwargs.get("blocked_by")})
        return commitment_id_for(mention.mention_ref)


def hinting(hint: str | None) -> ReconcileDecision:
    return ReconcileDecision(
        matched_commitment_id=None,
        confidence=1.0,
        relationship=MentionRelationship.ORIGINAL,
        canonical_title="Start the migration",
        inferred_state=CommitmentState.OPEN,
        blocking_hint=hint,
    )


def _fake_embed(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "weave_ingestion.embeddings.embed_documents", lambda texts: [[9.0] for _ in texts]
    )


def test_a_stated_dependency_becomes_an_edge_to_the_named_blocker(monkeypatch: Any) -> None:
    """The graph's whole value is this edge; nothing else had ever exercised it."""
    _fake_embed(monkeypatch)
    store = BlockerStore([{"commitment_id": "c-review", "similarity": 0.91}])

    reconcile_meeting(
        store, lambda text, candidates: hinting("cannot begin until the review"), [row()]
    )

    assert store.applied[0]["blocked_by"] == "c-review"


def test_a_weak_hint_match_creates_no_edge_rather_than_a_guessed_one(monkeypatch: Any) -> None:
    _fake_embed(monkeypatch)
    store = BlockerStore([{"commitment_id": "c-review", "similarity": 0.62}])

    reconcile_meeting(store, lambda text, candidates: hinting("something vaguely related"), [row()])

    assert store.applied[0]["blocked_by"] is None


def test_no_hint_means_no_edge_even_when_a_close_candidate_exists(monkeypatch: Any) -> None:
    """Topical similarity alone must never imply a dependency."""
    _fake_embed(monkeypatch)
    store = BlockerStore([{"commitment_id": "c-review", "similarity": 0.99}])

    reconcile_meeting(store, lambda text, candidates: hinting(None), [row()])

    assert store.applied[0]["blocked_by"] is None


def test_a_candidate_without_a_similarity_score_cannot_create_an_edge(monkeypatch: Any) -> None:
    """The lexical fallback returns no similarity, so an edge there would be
    ranked-by-recency rather than measured -- it must be dropped."""
    _fake_embed(monkeypatch)
    store = BlockerStore([{"commitment_id": "c-review"}])

    reconcile_meeting(store, lambda text, candidates: hinting("blocked on the review"), [row()])

    assert store.applied[0]["blocked_by"] is None


def test_an_embedding_failure_drops_the_edge_without_losing_the_mention(
    monkeypatch: Any,
) -> None:
    def explode(texts: Any) -> Any:
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr("weave_ingestion.embeddings.embed_documents", explode)
    store = BlockerStore([{"commitment_id": "c-review", "similarity": 0.95}])

    reconcile_meeting(store, lambda text, candidates: hinting("blocked on the review"), [row()])

    assert len(store.applied) == 1
    assert store.applied[0]["blocked_by"] is None


def test_a_hint_naming_a_candidate_outright_is_honoured_without_a_search(
    monkeypatch: Any,
) -> None:
    """Live models answer the dependency question with the candidate id as often
    as with a quote; embedding "c-review" as prose would score low and lose it."""

    def explode(texts: Any) -> Any:  # proves no similarity search was needed
        raise AssertionError("should not embed a hint that already names a candidate")

    monkeypatch.setattr("weave_ingestion.embeddings.embed_documents", explode)

    class Store(BlockerStore):
        def candidates_for(self, owner: str, embedding: Any) -> list[dict[str, Any]]:
            return [{"commitment_id": "c-review", "title": "Complete the review"}]

    store = Store([])
    reconcile_meeting(store, lambda text, candidates: hinting("c-review"), [row()])

    assert store.applied[0]["blocked_by"] == "c-review"


def test_a_hint_naming_an_id_outside_the_candidates_is_not_trusted(monkeypatch: Any) -> None:
    """Same fail-closed rule as matched_commitment_id: a hallucinated id is not
    a dependency, and it must not become one by a different route."""
    _fake_embed(monkeypatch)
    store = BlockerStore([{"commitment_id": "c-review", "similarity": 0.1}])

    reconcile_meeting(store, lambda text, candidates: hinting("c-invented"), [row()])

    assert store.applied[0]["blocked_by"] is None


def test_a_stated_precondition_reaches_the_judgement_verbatim() -> None:
    """The dependency people speak ("blocked because I need your email") is
    paraphrased out of the description, so it has to travel as its own field."""
    from weave_ingestion.commitments import judgement_text

    item = ActionItem(
        description="Pritam Mukherjee will request access from Jeremy.",
        source_text="you need to request for your access to Jeremy",
        blocked_on="blocked because I need Srija Dutta's email to attach",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1.0,
        resolution_turn_ref=1,
    )
    text = judgement_text(EnrichedActionItem(item=item, title="Request access", details=None))

    assert "Stated precondition:" in text
    assert "Srija Dutta's email" in text
    # The mention's own excerpt stays the clean description; this text is only
    # ever for judgement.
    assert text.splitlines()[0] == item.description


def test_an_item_without_a_precondition_adds_no_precondition_line() -> None:
    from weave_ingestion.commitments import judgement_text

    item = ActionItem(
        description="Send the report.",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1.0,
        resolution_turn_ref=1,
    )
    assert "precondition" not in judgement_text(EnrichedActionItem(item=item, title="Send"))
