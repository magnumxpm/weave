from types import SimpleNamespace

import pytest
from weave_common import Attendee

from agent.tools.speaker_resolution_tool import resolve_speaker


def context(*attendees: Attendee) -> SimpleNamespace:
    return SimpleNamespace(state={"attendees": [item.model_dump() for item in attendees]})


SARAH = Attendee(
    email="sarah@example.com", participant_id="participants/1", display_name="Sarah Chen"
)


def test_participant_id_match() -> None:
    assert resolve_speaker("participants/1", context(SARAH)) == {
        "email": "sarah@example.com",
        "confidence": 1.0,
        "method": "participant_id",
        "display_name": "Sarah Chen",
    }


def test_exact_display_name_match() -> None:
    result = resolve_speaker("sarah chen", context(SARAH))
    assert result["email"] == SARAH.email
    assert result["confidence"] == 0.95
    assert result["display_name"] == "Sarah Chen"


def test_first_name_fuzzy_match() -> None:
    result = resolve_speaker(
        "Sarah",
        context(
            SARAH,
            Attendee(
                email="miguel@example.com",
                participant_id="participants/2",
                display_name="Miguel Santos",
            ),
        ),
    )
    assert result["email"] == SARAH.email
    assert result["confidence"] == pytest.approx(0.9)


def test_ambiguous_first_name_is_rejected() -> None:
    result = resolve_speaker(
        "Alex",
        context(
            Attendee(email="a@example.com", participant_id="1", display_name="Alex Kim"),
            Attendee(email="b@example.com", participant_id="2", display_name="Alex Jones"),
        ),
    )
    assert result == {
        "email": None,
        "confidence": 0.0,
        "method": "ambiguous",
        "display_name": None,
    }


def test_missing_attendees_never_raises() -> None:
    assert resolve_speaker("Sarah", context()) == {
        "email": None,
        "confidence": 0.0,
        "method": "no_attendees",
        "display_name": None,
    }
