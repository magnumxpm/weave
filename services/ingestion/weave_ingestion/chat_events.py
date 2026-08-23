"""Pure parsing for Google Chat interaction events carried by Pub/Sub.

Two envelopes exist. A classic Chat app sends a flat `{type, user, space}`.
A Chat app configured as a Workspace add-on -- what the Chat API console
creates today -- nests everything under `chat` and drops `type` entirely: the
interaction is identified by which payload field is present. Both are accepted
so the deployment does not depend on which console produced the app.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Kind = Literal["added", "removed"]

# A direct message is the onboarding signal. `ADDED_TO_SPACE` only reaches an
# app that opted into joining spaces and group conversations -- which this one
# deliberately has not, being direct-message only -- so a DM install surfaces as
# a message. Onboarding is an idempotent upsert, so repeated messages from an
# already-onboarded user cost nothing.
FLAT_KINDS: dict[str, Kind] = {
    "ADDED_TO_SPACE": "added",
    "MESSAGE": "added",
    "REMOVED_FROM_SPACE": "removed",
}
ADDON_KINDS: dict[str, Kind] = {
    "addedToSpacePayload": "added",
    "messagePayload": "added",
    "removedFromSpacePayload": "removed",
}


class ChatEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Kind
    user_id: str
    email: str | None = None
    space_name: str


def _is_direct_message(space: dict[str, Any]) -> bool:
    return (
        bool(space.get("singleUserBotDm"))
        or space.get("spaceType") == "DIRECT_MESSAGE"
        or space.get("type") == "DM"
    )


def _unwrap(payload: dict[str, Any]) -> tuple[Kind, Any, Any] | None:
    """Reduce either envelope to (kind, user, space)."""
    if isinstance(chat := payload.get("chat"), dict):
        for field, kind in ADDON_KINDS.items():
            if isinstance(inner := chat.get(field), dict):
                # The space rides beside the payload on some interactions and
                # inside it on others; prefer whichever is present.
                return kind, chat.get("user"), chat.get("space") or inner.get("space")
        return None

    kind = FLAT_KINDS.get(payload.get("type") or payload.get("eventType"))
    return (kind, payload.get("user"), payload.get("space")) if kind else None


def parse_chat_event(payload: Any) -> ChatEvent | None:
    """Return an onboarding lifecycle event, or None for an acknowledged no-op."""
    if not isinstance(payload, dict):
        return None
    unwrapped = _unwrap(payload)
    if unwrapped is None:
        return None
    kind, user, space = unwrapped

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
        kind=kind,
        user_id=user_id,
        email=email if isinstance(email, str) and email.strip() else None,
        space_name=space_name,
    )
