"""Ground extracted person-references against trusted Meet attendees."""

from __future__ import annotations

from collections.abc import Sequence

from weave_common import (
    IDENTITY_CONFIDENCE_FLOOR,
    ActionItem,
    Attendee,
    Reference,
    ReferenceStatus,
)


def ground_references(item: ActionItem, attendees: Sequence[Attendee]) -> ActionItem:
    """Keep references to real attendees and demote every other identity."""
    by_email = {attendee.email.strip().casefold(): attendee for attendee in attendees}
    grounded: list[Reference] = []
    for reference in item.references:
        attendee = by_email.get((reference.email or "").strip().casefold())
        if (
            reference.status is ReferenceStatus.RESOLVED
            and attendee is not None
            and reference.confidence >= IDENTITY_CONFIDENCE_FLOOR
        ):
            grounded.append(
                reference.model_copy(
                    update={
                        "email": attendee.email,
                        "display_name": attendee.display_name,
                    }
                )
            )
        else:
            grounded.append(
                Reference(
                    mention=reference.mention,
                    turn_ref=reference.turn_ref,
                    status=ReferenceStatus.UNKNOWN,
                )
            )
    return item.model_copy(update={"references": grounded})
