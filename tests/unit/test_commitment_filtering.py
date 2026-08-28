from datetime import date

import pytest
from pydantic import ValidationError
from weave_common import (
    ActionItem,
    ActionType,
    CommitmentStatus,
    EnrichedActionItem,
    MeetingInsights,
    MeetingSummaryContent,
)


def item(status: CommitmentStatus, **overrides: object) -> ActionItem:
    values: dict[str, object] = {
        "description": "Send the report",
        "action_type": ActionType.TASK,
        "status": status,
        "owner_email": "Owner@Example.com",
        "owner_confidence": 0.95,
        "commitment_turn_ref": 1,
        "resolution_turn_ref": 2 if status is CommitmentStatus.ACCEPTED else None,
    }
    values.update(overrides)
    return ActionItem.model_validate(values)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CommitmentStatus.ACCEPTED, True),
        (CommitmentStatus.REASSIGNED, True),
        (CommitmentStatus.DECLINED, False),
        (CommitmentStatus.DEFERRED, False),
        (CommitmentStatus.UNRESOLVED, False),
    ],
)
def test_actionable_statuses(status: CommitmentStatus, expected: bool) -> None:
    assert item(status).is_actionable() is expected


def test_accepted_requires_resolution_turn_ref() -> None:
    with pytest.raises(ValidationError):
        item(CommitmentStatus.ACCEPTED, resolution_turn_ref=None)


def test_items_for_owner_is_case_insensitive_and_filters_non_actionable() -> None:
    insights = MeetingInsights(
        conference_record_id="conferenceRecords/1",
        meeting_date=date(2026, 8, 22),
        summary=MeetingSummaryContent(overview="A meeting summary"),
        items=[
            item(CommitmentStatus.ACCEPTED),
            item(CommitmentStatus.DECLINED),
            item(CommitmentStatus.REASSIGNED, owner_email="other@example.com"),
        ],
    )

    assert insights.items_for_owner("owner@example.COM") == [insights.items[0]]
    assert insights.items_for_owner("unknown@example.com") == []


def test_owner_confidence_is_required() -> None:
    values = item(CommitmentStatus.REASSIGNED).model_dump()
    del values["owner_confidence"]
    with pytest.raises(ValidationError):
        ActionItem.model_validate(values)


def test_enriched_display_fields_are_additive_and_length_bounded() -> None:
    action = item(CommitmentStatus.ACCEPTED)
    assert EnrichedActionItem(item=action).title is None
    with pytest.raises(ValidationError):
        EnrichedActionItem(item=action, details="x" * 701)


def test_meeting_summary_is_required_and_bounded() -> None:
    with pytest.raises(ValidationError):
        MeetingInsights.model_validate(
            {
                "conference_record_id": "conferenceRecords/1",
                "meeting_date": "2026-08-22",
                "items": [],
            }
        )
    with pytest.raises(ValidationError):
        MeetingSummaryContent(overview="")
    with pytest.raises(ValidationError):
        MeetingSummaryContent(overview="Summary", topics=[str(index) for index in range(13)])
