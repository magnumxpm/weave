from __future__ import annotations

from datetime import date

import pytest
from weave_common import (
    ActionItem,
    ActionType,
    Attendee,
    CommitmentStatus,
    MeetingInsights,
    Reference,
    ReferenceStatus,
)

from agent.auth.reference_grounding import ground_references


def item_with(reference: Reference) -> ActionItem:
    return ActionItem(
        description="Follow up with Sarah",
        action_type=ActionType.FOLLOW_UP,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=0.95,
        resolution_turn_ref=2,
        deadline=date(2026, 8, 30),
        references=[reference],
    )


ATTENDEE = Attendee(
    email="sarah@example.com",
    participant_id="participants/1",
    display_name="Sarah Chen",
)


def resolved(*, email: str = ATTENDEE.email, confidence: float = 0.95) -> Reference:
    return Reference(
        mention="her",
        turn_ref=1,
        status=ReferenceStatus.RESOLVED,
        email=email,
        display_name="Model-provided name",
        confidence=confidence,
    )


@pytest.mark.parametrize(
    "reference",
    [resolved(email="invented@example.com"), resolved(confidence=0.7)],
)
def test_untrusted_reference_is_demoted_without_losing_provenance(reference: Reference) -> None:
    grounded = ground_references(item_with(reference), [ATTENDEE]).references[0]
    assert grounded == Reference(
        mention="her",
        turn_ref=1,
        status=ReferenceStatus.UNKNOWN,
    )


def test_display_name_is_taken_from_meet_not_the_model() -> None:
    grounded = ground_references(item_with(resolved()), [ATTENDEE]).references[0]
    assert grounded.email == ATTENDEE.email
    assert grounded.display_name == ATTENDEE.display_name
    assert grounded.confidence == 0.95


def test_half_identified_reference_normalizes_to_unknown() -> None:
    half = Reference(
        mention="her",
        turn_ref=1,
        status=ReferenceStatus.RESOLVED,
        display_name="Sarah",
        confidence=1.0,
    )
    assert half.status is ReferenceStatus.UNKNOWN
    assert half.display_name is None
    assert half.confidence == 0.0

    stray = Reference(
        mention="them",
        turn_ref=1,
        status=ReferenceStatus.UNKNOWN,
        email="someone@example.com",
    )
    assert stray.email is None


def test_an_inconsistent_reference_never_costs_the_meeting() -> None:
    # Raising here would fail MeetingInsights validation, which fails
    # extraction, which loses every item for every owner.
    insights = MeetingInsights.model_validate(
        {
            "conference_record_id": "conferenceRecords/one",
            "meeting_date": "2026-08-23",
            "summary": {"overview": "A meeting summary"},
            "items": [
                {
                    **item_with(resolved()).model_dump(mode="json"),
                    "references": [{"mention": "me", "turn_ref": 4, "status": "resolved"}],
                }
            ],
        }
    )
    assert insights.items[0].references[0].status is ReferenceStatus.UNKNOWN
