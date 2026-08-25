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
    message_text: str | None = None
    message_name: str | None = None


class ChatClickEvent(BaseModel):
    """A platform-authenticated action-card click."""

    model_config = ConfigDict(frozen=True)

    function: str
    conference_id: str | None = None
    item_index: str | None = None
    user_id: str
    # Commitment cards address an item directly; meeting cards predate
    # commitments and still address one by conference plus position.
    commitment_id: str | None = None
    rendered_ids: str | None = None


def _is_direct_message(space: dict[str, Any]) -> bool:
    return (
        bool(space.get("singleUserBotDm"))
        or space.get("spaceType") == "DIRECT_MESSAGE"
        or space.get("type") == "DM"
    )


def _unwrap(payload: dict[str, Any]) -> tuple[Kind, Any, Any, Any] | None:
    """Reduce either envelope to (kind, user, space, message)."""
    if isinstance(chat := payload.get("chat"), dict):
        for field, kind in ADDON_KINDS.items():
            if isinstance(inner := chat.get(field), dict):
                # The space rides beside the payload on some interactions and
                # inside it on others; prefer whichever is present.
                message = inner.get("message") if field == "messagePayload" else None
                return kind, chat.get("user"), chat.get("space") or inner.get("space"), message
        return None

    kind = FLAT_KINDS.get(payload.get("type") or payload.get("eventType"))
    if kind is None:
        return None
    return kind, payload.get("user"), payload.get("space"), payload.get("message")


def _parameters(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        return {
            str(item.get("key")): str(item.get("value"))
            for item in value
            if isinstance(item, dict) and item.get("key") is not None
        }
    return {}


def _parse_click(payload: dict[str, Any]) -> ChatClickEvent | None:
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else None
    common = payload.get("commonEventObject")
    common = common if isinstance(common, dict) else {}
    # buttonClickedPayload is the documented signal, but the HTTP envelope has
    # never been observed carrying a click yet; invokedFunction only appears on
    # interactions, so either marks the payload as one.
    is_addon_click = chat is not None and (
        isinstance(chat.get("buttonClickedPayload"), dict) or bool(common.get("invokedFunction"))
    )
    is_classic_click = (payload.get("type") or payload.get("eventType")) == "CARD_CLICKED"
    if not is_addon_click and not is_classic_click:
        return None

    user = chat.get("user") if chat is not None else payload.get("user")
    if not isinstance(user, dict) or user.get("type") == "BOT":
        return None
    user_name = user.get("name")
    if not isinstance(user_name, str) or not user_name.startswith("users/"):
        return None
    user_id = user_name.removeprefix("users/")
    if not user_id.isdigit():
        return None

    if chat is not None:
        button = chat.get("buttonClickedPayload") or {}
        action = button.get("action") if isinstance(button, dict) else {}
        action = action if isinstance(action, dict) else {}
        function = common.get("invokedFunction") or action.get("function")
        parameters = _parameters(common.get("parameters") or action.get("parameters"))
    else:
        action = payload.get("action")
        action = action if isinstance(action, dict) else {}
        function = action.get("actionMethodName") or action.get("function")
        parameters = _parameters(action.get("parameters"))

    # HTTP-deployed add-on apps require onClick.action.function to be the
    # endpoint URL, so the logical action rides in parameters. The old field is
    # the fallback for classic apps and for cards minted before this change.
    action_name = parameters.get("weave_action") or function
    if not isinstance(action_name, str) or not action_name:
        return None
    return ChatClickEvent(
        function=action_name,
        conference_id=parameters.get("conference_id"),
        item_index=parameters.get("item_index"),
        user_id=user_id,
        commitment_id=parameters.get("commitment_id"),
        rendered_ids=parameters.get("rendered_ids"),
    )


def parse_chat_event(payload: Any) -> ChatEvent | ChatClickEvent | None:
    """Return an onboarding lifecycle event, or None for an acknowledged no-op."""
    if not isinstance(payload, dict):
        return None
    if click := _parse_click(payload):
        return click
    unwrapped = _unwrap(payload)
    if unwrapped is None:
        return None
    kind, user, space, message = unwrapped

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
    message = message if isinstance(message, dict) else {}
    message_text = message.get("text")
    message_name = message.get("name")
    return ChatEvent(
        kind=kind,
        user_id=user_id,
        email=email if isinstance(email, str) and email.strip() else None,
        space_name=space_name,
        message_text=message_text if isinstance(message_text, str) else None,
        message_name=message_name if isinstance(message_name, str) else None,
    )
