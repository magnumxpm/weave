"""Fail-closed conversion from extracted ownership to a search principal."""

from __future__ import annotations

from typing import Literal

from weave_common import IDENTITY_CONFIDENCE_FLOOR

from agent.context_sources.base import SearchPrincipal

PrincipalFailureReason = Literal["no_email", "low_confidence", "not_attendee"]


class PrincipalResolutionError(ValueError):
    def __init__(self, reason: PrincipalFailureReason) -> None:
        self.reason = reason
        super().__init__(reason)


def resolve_principal(
    owner_email: str | None, confidence: float, attendee_emails: set[str]
) -> SearchPrincipal:
    """Resolve only sufficiently confident owners present in trusted Meet data."""
    if not owner_email or not owner_email.strip():
        raise PrincipalResolutionError("no_email")
    if confidence < IDENTITY_CONFIDENCE_FLOOR:
        raise PrincipalResolutionError("low_confidence")

    normalized_email = owner_email.strip().casefold()
    normalized_attendees = {email.strip().casefold() for email in attendee_emails}
    if normalized_email not in normalized_attendees:
        raise PrincipalResolutionError("not_attendee")
    return SearchPrincipal(email=normalized_email)
