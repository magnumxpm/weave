"""Delivery contract and pure Google Chat Card v2 renderer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from weave_common import CommitmentStatus, EnrichedOwnerBundle, ReferenceStatus

from weave_ingestion.firestore_client import OnboardedUser


@dataclass(frozen=True)
class MeetingHeader:
    """Ingestion-owned meeting metadata that never enters agent context."""

    title: str | None = None
    started_at: datetime | None = None
    participant_names: tuple[str, ...] = ()


class Deliverer(ABC):
    @abstractmethod
    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
        meeting: MeetingHeader | None = None,
    ) -> str:
        """Deliver one owner bundle and return the provider delivery ID."""


def _decorated_text(
    text: str,
    *,
    top_label: str | None = None,
    start_icon: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"text": text, "wrapText": True}
    if top_label:
        value["topLabel"] = top_label
    if start_icon:
        value["startIcon"] = {"materialIcon": {"name": start_icon}}
    return {"decoratedText": value}


def _participant_line(names: tuple[str, ...]) -> str | None:
    unique = tuple(dict.fromkeys(name.strip() for name in names if name.strip()))
    if not unique:
        return None
    if len(unique) == 1:
        return f"with {unique[0]}"
    if len(unique) == 2:
        return f"with {unique[0]} and {unique[1]}"
    return f"with {unique[0]}, {unique[1]}, and {len(unique) - 2} more"


def _status(status: CommitmentStatus) -> tuple[str, str]:
    if status is CommitmentStatus.ACCEPTED:
        return "Accepted by you", "check_circle"
    if status is CommitmentStatus.REASSIGNED:
        return "Reassigned to you", "check_circle"
    return "Awaiting your response", "schedule"


def _action_button(
    *,
    icon: str,
    alt_text: str,
    function: str,
    conference_id: str,
    item_index: int,
    endpoint_url: str = "",
) -> dict[str, Any]:
    # An HTTP-deployed add-on routes a click to onClick.action.function itself,
    # so for those apps it must be the endpoint URL -- a bare name gives the
    # client nowhere to send the click and it dies before reaching any server.
    # The logical action always rides in parameters as weave_action.
    return {
        "icon": {"materialIcon": {"name": icon}},
        "altText": alt_text,
        "onClick": {
            "action": {
                "function": endpoint_url or function,
                "parameters": [
                    {"key": "weave_action", "value": function},
                    {"key": "conference_id", "value": conference_id},
                    {"key": "item_index", "value": str(item_index)},
                ],
            }
        },
    }


def build_card(
    bundle: EnrichedOwnerBundle,
    meeting: MeetingHeader | None = None,
    *,
    button_url: str = "",
) -> dict[str, Any]:
    """Render one owner-scoped bundle without exposing retrieved context."""
    header: dict[str, Any] = {"title": "Action items for you"}
    if meeting is not None:
        subtitle_parts = []
        if meeting.title:
            subtitle_parts.append(meeting.title)
        if meeting.started_at:
            subtitle_parts.append(meeting.started_at.strftime("%H:%M"))
        if subtitle_parts:
            header["subtitle"] = " • ".join(subtitle_parts)

    sections: list[dict[str, Any]] = []
    if meeting is not None and (participants := _participant_line(meeting.participant_names)):
        sections.append({"widgets": [_decorated_text(participants, start_icon="group")]})

    conference_id = bundle.conference_record_id.rsplit("/", 1)[-1]
    for item_index, enriched_item in enumerate(bundle.items, start=1):
        item = enriched_item.item
        has_title = bool(enriched_item.title and enriched_item.title.strip())
        title = enriched_item.title if has_title else item.description
        status_text, status_icon = _status(item.status)
        widgets: list[dict[str, Any]] = [
            _decorated_text(
                f"{item_index}. {title}",
                top_label=status_text,
                start_icon=status_icon,
            )
        ]
        if enriched_item.details:
            widgets.append(_decorated_text(enriched_item.details, top_label="Details"))

        unknown_references = [
            reference
            for reference in item.references
            if reference.status is ReferenceStatus.UNKNOWN
        ]
        if not has_title and unknown_references:
            widgets.append(
                _decorated_text(
                    "\n".join(
                        f'"{reference.mention}" (turn {reference.turn_ref}) '
                        "could not be identified from the transcript"
                        for reference in unknown_references
                    ),
                    top_label="Unidentified",
                )
            )

        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        _action_button(
                            icon="check",
                            alt_text="Accept",
                            function="accept_item",
                            conference_id=conference_id,
                            item_index=item_index,
                            endpoint_url=button_url,
                        ),
                        _action_button(
                            icon="done_all",
                            alt_text="Mark done",
                            function="mark_done",
                            conference_id=conference_id,
                            item_index=item_index,
                            endpoint_url=button_url,
                        ),
                        _action_button(
                            icon="close",
                            alt_text="Decline",
                            function="decline_item",
                            conference_id=conference_id,
                            item_index=item_index,
                            endpoint_url=button_url,
                        ),
                    ]
                }
            }
        )
        sections.append(
            {
                "collapsible": True,
                "uncollapsibleWidgetsCount": 1,
                "widgets": widgets,
            }
        )

    sections.append({"widgets": [_decorated_text("Only visible to you", start_icon="lock")]})
    return {
        "cardId": f"weave-{conference_id}",
        "card": {"header": header, "sections": sections},
    }
