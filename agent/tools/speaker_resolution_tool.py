"""Resolve transcript speakers only against trusted Meet attendees."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from google.adk.tools.tool_context import ToolContext
from weave_common import Attendee


def _attendees_from_state(tool_context: ToolContext) -> list[Attendee]:
    raw_attendees = tool_context.state.get("attendees", [])
    attendees: list[Attendee] = []
    for raw in raw_attendees:
        try:
            attendees.append(raw if isinstance(raw, Attendee) else Attendee.model_validate(raw))
        except (TypeError, ValueError):
            continue
    return attendees


def _result(email: str | None, confidence: float, method: str) -> dict[str, Any]:
    return {"email": email, "confidence": confidence, "method": method}


def resolve_speaker(speaker: str, tool_context: ToolContext) -> dict[str, Any]:
    """Resolve a participant ID or spoken name against trusted attendee state."""
    attendees = _attendees_from_state(tool_context)
    if not attendees:
        return _result(None, 0.0, "no_attendees")

    candidate = speaker.strip().casefold()
    participant_matches = [
        attendee for attendee in attendees if attendee.participant_id.casefold() == candidate
    ]
    if len(participant_matches) == 1:
        return _result(participant_matches[0].email, 1.0, "participant_id")

    name_matches = [
        attendee for attendee in attendees if attendee.display_name.strip().casefold() == candidate
    ]
    if len(name_matches) == 1:
        return _result(name_matches[0].email, 0.95, "display_name")
    if len(name_matches) > 1:
        return _result(None, 0.0, "ambiguous")

    ranked: list[tuple[float, Attendee]] = []
    for attendee in attendees:
        full_name = attendee.display_name.strip().casefold()
        first_name = full_name.split(maxsplit=1)[0] if full_name else ""
        score = max(
            SequenceMatcher(None, candidate, full_name).ratio(),
            SequenceMatcher(None, candidate, first_name).ratio(),
        )
        ranked.append((score, attendee))
    ranked.sort(key=lambda entry: entry[0], reverse=True)

    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] <= 0.05:
        return _result(None, 0.0, "ambiguous")

    score, attendee = ranked[0]
    return _result(attendee.email, round(score * 0.9, 4), "fuzzy_name")
