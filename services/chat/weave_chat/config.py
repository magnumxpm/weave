"""Settings for the Chat endpoint alone.

Deliberately not `weave_ingestion.config.Settings`: that model forbids extras and
requires the pipeline's agent-engine and Model Armor values, none of which exist
in a Chat-only environment. Sharing it would couple this service's startup to
configuration it must never need.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


class ChatSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    project_number: str
    chat_audience: str
    chat_events_topic: str


def settings_from_env() -> ChatSettings:
    return ChatSettings(
        project_id=os.environ["PROJECT_ID"],
        # Names which add-ons service agent may sign requests; see jwt_auth.
        project_number=os.environ["PROJECT_NUMBER"],
        # The audience Chat mints tokens for: this service's own URL.
        chat_audience=os.environ["CHAT_AUDIENCE"],
        chat_events_topic=os.environ["CHAT_EVENTS_TOPIC"],
    )
