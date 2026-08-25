"""Platform-identity seam for copilot invocations."""

from __future__ import annotations

import re
from typing import Any

WORKSPACE_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def principal_from_invocation(user_id: str) -> str | None:
    """Accept only a platform-supplied, email-shaped ADK user id."""
    if not isinstance(user_id, str):
        return None
    normalized = user_id.strip().casefold()
    return normalized if WORKSPACE_EMAIL.fullmatch(normalized) else None


def seed_copilot_principal(callback_context: Any) -> None:
    """Overwrite session state every turn; stale principals never survive.

    Refusal writes an empty principal rather than deleting the key. ADK's
    `State` supports `__setitem__` but neither `pop` nor `__delitem__`, so
    deleting raises `AttributeError` and takes down the very invocation that
    was supposed to fail closed -- while leaving the previous turn's principal
    in place. An unusable value is the assignment this class can actually make.
    """
    callback_context.state["copilot_principal"] = (
        principal_from_invocation(callback_context.user_id) or ""
    )
    return None
