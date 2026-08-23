from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError
from weave_common import (
    ActionItem,
    ActionType,
    Attendee,
    CommitmentStatus,
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


def test_reference_status_requires_matching_identity_shape() -> None:
    with pytest.raises(ValidationError):
        Reference(
            mention="her",
            turn_ref=1,
            status=ReferenceStatus.RESOLVED,
            display_name="Sarah",
        )
    with pytest.raises(ValidationError):
        Reference(
            mention="them",
            turn_ref=1,
            status=ReferenceStatus.UNKNOWN,
            email="someone@example.com",
        )
