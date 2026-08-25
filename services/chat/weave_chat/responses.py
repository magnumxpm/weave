"""Chat interaction responses, in whichever dialect the request arrived in.

Google Chat has two envelope shapes -- a classic app's flat payload and a
Workspace add-on's payload nested under `chat` -- and they take different
response shapes for the same action. Which one this deployment gets depends on
how the app was registered in the console, which no code here can read.

So the dialect is chosen from the request itself rather than from configuration.
That way a console change cannot silently produce responses Chat ignores, and
the `envelope_dialect` log line records what actually arrived.
"""

from __future__ import annotations

from typing import Any


def is_addon_envelope(body: Any) -> bool:
    """Add-on payloads nest everything under `chat`; the same signal parse uses."""
    return isinstance(body, dict) and isinstance(body.get("chat"), dict)


def dialect_of(body: Any) -> str:
    return "addon" if is_addon_envelope(body) else "classic"


def update_message(card: dict[str, Any], *, addon: bool) -> dict[str, Any]:
    """Replace the clicked message in place, so the card reflects the new state."""
    if addon:
        return {
            "hostAppDataAction": {
                "chatDataAction": {"updateMessageAction": {"message": {"cardsV2": [card]}}}
            }
        }
    return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "cardsV2": [card]}


def new_message(text: str, *, addon: bool) -> dict[str, Any]:
    """Post a fresh message. Never answer a click with an empty body: Chat reads
    silence as failure and shows the user "unable to process your request"."""
    if addon:
        return {
            "hostAppDataAction": {
                "chatDataAction": {"createMessageAction": {"message": {"text": text}}}
            }
        }
    return {"actionResponse": {"type": "NEW_MESSAGE"}, "text": text}
