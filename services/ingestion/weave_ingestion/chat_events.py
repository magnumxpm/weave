"""Pure parsing for Google Chat interaction events carried by Pub/Sub."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ChatEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["added", "removed"]
    user_id: str
    email: str | None = None
    space_name: str


def _is_direct_message(space: dict[str, Any]) -> bool:
    return bool(space.get("singleUserBotDm")) or space.get("spaceType") == "DIRECT_MESSAGE"


# A direct message is the onboarding signal. `ADDED_TO_SPACE` only reaches an
# app that opted into joining spaces and group conversations -- which this one
# deliberately has not, being direct-message only -- so a DM install surfaces as
# `MESSAGE` instead. Onboarding is an idempotent upsert, so repeated messages
# from an already-onboarded user cost nothing.
ONBOARDING_TYPES = {"ADDED_TO_SPACE", "MESSAGE"}
OFFBOARDING_TYPES = {"REMOVED_FROM_SPACE"}


def parse_chat_event(payload: Any) -> ChatEvent | None:
    """Return an onboarding lifecycle event, or None for an acknowledged no-op."""
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type") or payload.get("eventType")
    if event_type not in ONBOARDING_TYPES | OFFBOARDING_TYPES:
        return None

    user = payload.get("user")
    space = payload.get("space")
    if not isinstance(user, dict) or not isinstance(space, dict) or not _is_direct_message(space):
        return None
    if user.get("type") == "BOT":
        return None  # never onboard an app off its own traffic

    user_name = user.get("name")
    space_name = space.get("name")
    if not isinstance(user_name, str) or not user_name.startswith("users/"):
        return None
    user_id = user_name.removeprefix("users/")
    if (
        not user_id.isdigit()
        or not isinstance(space_name, str)
        or not space_name.startswith("spaces/")
    ):
        return None

    email = user.get("email")
    return ChatEvent(
        kind="added" if event_type in ONBOARDING_TYPES else "removed",
        user_id=user_id,
        email=email if isinstance(email, str) and email.strip() else None,
        space_name=space_name,
    )
