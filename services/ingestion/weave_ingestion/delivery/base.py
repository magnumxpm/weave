"""Delivery contract and pure Google Chat Card v2 renderer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from weave_common import EnrichedOwnerBundle, ReferenceStatus

from weave_ingestion.firestore_client import OnboardedUser


class Deliverer(ABC):
    @abstractmethod
    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
    ) -> str:
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
        ]
        if item.deadline is not None:
            widgets.append(_decorated_text("Deadline", item.deadline.isoformat()))

        unknown_references = [
            reference
            for reference in item.references
            if reference.status is ReferenceStatus.UNKNOWN
        ]
        if unknown_references:
            widgets.append(
                _decorated_text(
                    "Unidentified",
                    "\n".join(
                        f'"{reference.mention}" (turn {reference.turn_ref}) '
                        "could not be identified from the transcript"
                        for reference in unknown_references
                    ),
                )
            )

        if bundle.enriched and enriched_item.matches:
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
