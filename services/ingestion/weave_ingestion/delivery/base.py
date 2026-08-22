"""Delivery contract and pure Google Chat Card v2 renderer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from weave_common import EnrichedOwnerBundle


class Deliverer(ABC):
    @abstractmethod
    def deliver(self, owner_email: str, bundle: EnrichedOwnerBundle) -> str:
        """Deliver one owner bundle and return the provider delivery ID."""


def _decorated_text(label: str, value: str) -> dict[str, Any]:
    return {"decoratedText": {"topLabel": label, "text": value, "wrapText": True}}


def build_card(bundle: EnrichedOwnerBundle) -> dict[str, Any]:
    """Render one owner-scoped bundle into a Card v2 payload."""
    sections: list[dict[str, Any]] = []
    for enriched_item in bundle.items:
        item = enriched_item.item
        widgets: list[dict[str, Any]] = [
            _decorated_text("Action", item.description),
            _decorated_text("Status", item.status.value),
            _decorated_text(
                "Commitment turn",
                str(item.commitment_turn_ref)
                if item.commitment_turn_ref is not None
                else "Unknown",
            ),
        ]
        if item.deadline is not None:
            widgets.append(_decorated_text("Deadline", item.deadline.isoformat()))

        if not bundle.enriched:
            context_text = "Related context unavailable"
        elif not enriched_item.matches:
            context_text = "No related context found"
        else:
            context_text = "\n".join(
                f"{match.source_name}: {match.title}" for match in enriched_item.matches
            )
        widgets.append(_decorated_text("Related context", context_text))
        sections.append({"widgets": widgets})

    return {
        "cardId": f"weave-{bundle.conference_record_id.rsplit('/', 1)[-1]}",
        "card": {
            "header": {
                "title": f"Your action items from {bundle.meeting_date.isoformat()}",
                "subtitle": bundle.conference_record_id,
            },
            "sections": sections,
        },
    }
