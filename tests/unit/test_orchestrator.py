from __future__ import annotations

from datetime import date

from weave_common import (
    ActionItem,
    ActionType,
    Attendee,
    CommitmentStatus,
    EnrichedActionItem,
    MeetingInsights,
    OwnerItemList,
    PipelineRequest,
    TranscriptTurn,
)

from agent.agents.orchestrator import enforce_owner_scope, run_pipeline
from agent.context_sources.base import SearchPrincipal


def action(
    owner: str,
    *,
    confidence: float = 0.95,
    status: CommitmentStatus = CommitmentStatus.ACCEPTED,
    description: str = "Send report",
) -> ActionItem:
    return ActionItem(
        description=description,
        action_type=ActionType.TASK,
        status=status,
        owner_email=owner,
        owner_confidence=confidence,
        commitment_turn_ref=1,
        resolution_turn_ref=2 if status is CommitmentStatus.ACCEPTED else None,
    )


def request(*emails: str) -> PipelineRequest:
    return PipelineRequest(
        transcript_turns=[
            TranscriptTurn(turn_index=0, participant_id="1", speaker_name="Host", text="Meeting")
        ],
        conference_record_id="conferenceRecords/1",
        meeting_date=date(2026, 8, 22),
        attendees=[
            Attendee(email=email, participant_id=str(index), display_name=email.split("@")[0])
            for index, email in enumerate(emails)
        ],
    )


def extractor(*items: ActionItem):
    def extract(req: PipelineRequest) -> MeetingInsights:
        return MeetingInsights(
            conference_record_id=req.conference_record_id,
            meeting_date=req.meeting_date,
            items=list(items),
        )

    return extract


def passthrough(principal: SearchPrincipal, items: list[ActionItem]) -> OwnerItemList:
    return OwnerItemList(
        owner_email=principal.email,
        items=[EnrichedActionItem(item=item, matches=[]) for item in items],
    )


def test_transcript_only_attendee_is_refused_principal() -> None:
    result = run_pipeline(
        request("real@example.com"),
        extract=extractor(action("transcript-only@example.com")),
        enrich=lambda principal, items: (_ for _ in ()).throw(AssertionError("searched")),
    )
    assert result.bundles[0].enriched is False
    assert result.bundles[0].skip_reason == "not_attendee"


def test_confidence_boundary() -> None:
    low = run_pipeline(
        request("owner@example.com"),
        extract=extractor(action("owner@example.com", confidence=0.84)),
        enrich=passthrough,
    )
    exact = run_pipeline(
        request("owner@example.com"),
        extract=extractor(action("owner@example.com", confidence=0.85)),
        enrich=passthrough,
    )
    assert low.bundles[0].skip_reason == "low_confidence"
    assert exact.bundles[0].enriched is True


def test_enrichment_session_state_contains_only_owner_items() -> None:
    captured: dict[str, list[ActionItem]] = {}

    def capture(principal: SearchPrincipal, items: list[ActionItem]) -> OwnerItemList:
        captured[principal.email] = items
        return passthrough(principal, items)

    run_pipeline(
        request("a@example.com", "b@example.com"),
        extract=extractor(
            action("a@example.com", description="A only"),
            action("b@example.com", description="B only"),
        ),
        enrich=capture,
    )
    assert [item.description for item in captured["a@example.com"]] == ["A only"]
    assert [item.description for item in captured["b@example.com"]] == ["B only"]


def test_enforce_owner_scope_drops_foreign_modified_and_duplicate_items() -> None:
    original = action("a@example.com", description="original")
    foreign = action("b@example.com", description="foreign")
    modified = action("a@example.com", description="modified")
    result = OwnerItemList(
        owner_email="a@example.com",
        items=[
            EnrichedActionItem(item=original),
            EnrichedActionItem(item=foreign),
            EnrichedActionItem(item=modified),
            EnrichedActionItem(item=original),
        ],
    )
    scoped = enforce_owner_scope("a@example.com", [original], result)
    assert [item.item for item in scoped] == [original]


def test_one_enrichment_failure_does_not_sink_other_owner() -> None:
    def enrich(principal: SearchPrincipal, items: list[ActionItem]) -> OwnerItemList:
        if principal.email == "a@example.com":
            raise RuntimeError("failed")
        return passthrough(principal, items)

    result = run_pipeline(
        request("a@example.com", "b@example.com"),
        extract=extractor(action("a@example.com"), action("b@example.com")),
        enrich=enrich,
    )
    bundles = {bundle.owner_email: bundle for bundle in result.bundles}
    assert bundles["a@example.com"].skip_reason == "enrichment_error"
    assert bundles["b@example.com"].enriched is True


def test_non_actionable_and_unroutable_items_are_counted_as_dropped() -> None:
    result = run_pipeline(
        request("owner@example.com"),
        extract=extractor(
            action("owner@example.com"),
            action("owner@example.com", status=CommitmentStatus.DECLINED),
            action("", status=CommitmentStatus.REASSIGNED),
        ),
        enrich=passthrough,
    )
    assert result.dropped_item_count == 2
    assert len(result.bundles) == 1
