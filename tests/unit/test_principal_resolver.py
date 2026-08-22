import pytest

from agent.auth.principal_resolver import PrincipalResolutionError, resolve_principal


@pytest.mark.parametrize(
    ("email", "confidence", "attendees", "reason"),
    [
        (None, 1.0, {"owner@example.com"}, "no_email"),
        ("owner@example.com", 0.84, {"owner@example.com"}, "low_confidence"),
        ("outsider@example.com", 1.0, {"owner@example.com"}, "not_attendee"),
    ],
)
def test_principal_resolution_fails_closed(
    email: str | None, confidence: float, attendees: set[str], reason: str
) -> None:
    with pytest.raises(PrincipalResolutionError) as raised:
        resolve_principal(email, confidence, attendees)
    assert raised.value.reason == reason


def test_threshold_and_email_matching_are_exact() -> None:
    principal = resolve_principal("Owner@Example.com", 0.85, {"owner@example.COM"})
    assert principal.email == "owner@example.com"
