from google.adk.sessions.state import State

from agent.copilot.principal import principal_from_invocation, seed_copilot_principal
from agent.copilot.tools import _principal


class Context:
    """Carry ADK's real State: a plain dict has `pop`, and State does not."""

    def __init__(self, user_id: str, state: dict[str, str]) -> None:
        self.user_id = user_id
        self.state = State(dict(state), {})


def test_platform_principal_is_normalized_and_non_email_ids_are_refused() -> None:
    assert principal_from_invocation(" Owner@Example.COM ") == "owner@example.com"
    assert principal_from_invocation("users/123") is None
    assert principal_from_invocation("") is None
    assert principal_from_invocation("owner @example.com") is None


def test_callback_overwrites_stale_session_principal() -> None:
    valid = Context("new@example.com", {"copilot_principal": "old@example.com"})
    seed_copilot_principal(valid)
    assert valid.state["copilot_principal"] == "new@example.com"


def test_refused_identity_neutralizes_the_previous_turns_principal() -> None:
    """A non-email id must disarm state, not raise and leave the old one behind.

    Gemini Enterprise supplying an opaque user id lands here on every turn, so
    this path has to survive rather than crash the invocation.
    """
    invalid = Context("users/123", {"copilot_principal": "old@example.com"})
    seed_copilot_principal(invalid)

    assert invalid.state["copilot_principal"] == ""
    assert _principal(invalid) is None
