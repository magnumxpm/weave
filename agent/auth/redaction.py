"""Pure defense-in-depth filters for data entering enrichment."""

from __future__ import annotations

from weave_common import ACTIONABLE_STATUSES, ActionItem


def items_for_enrichment(owner_email: str, items: list[ActionItem]) -> list[ActionItem]:
    normalized_owner = owner_email.strip().casefold()
    return [
        item
        for item in items
        if item.status in ACTIONABLE_STATUSES
        and item.owner_email is not None
        and item.owner_email.strip().casefold() == normalized_owner
    ]
