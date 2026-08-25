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
