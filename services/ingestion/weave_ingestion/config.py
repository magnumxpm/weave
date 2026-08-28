"""Environment-driven configuration, validated before the app serves traffic."""

from __future__ import annotations

import os
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    region: str
    agent_engine_id: str
    copilot_engine_id: str = ""
    chat_button_url: str = ""
    pubsub_push_sa: str
    pubsub_push_audience: str
    model_armor_input_template: str
    artifact_source: Literal["live", "fixture"]
    fixture_dir: str = ""
    delivery_mode: Literal["chat", "log"]
    subscription_job_name: str = ""
    # Admin impersonated for directory lookups only. The Meet fetch impersonates
    # the user whose subscription produced each event, never this account.
    admin_subject: str = ""
    workspace_timezone: str

    @model_validator(mode="after")
    def source_specific_requirements(self) -> Settings:
        if self.artifact_source == "fixture" and not self.fixture_dir:
            raise ValueError("fixture_dir is required when artifact_source=fixture")
        # Both live reads and Chat delivery resolve identities through the
        # Directory API, which is a delegated call needing a subject.
        if (self.artifact_source == "live" or self.delivery_mode == "chat") and (
            not self.admin_subject
        ):
            raise ValueError("admin_subject is required for live reads or chat delivery")
        try:
            ZoneInfo(self.workspace_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("workspace_timezone must be a valid IANA timezone") from error
        return self


def settings_from_env() -> Settings:
    return Settings(
        project_id=os.environ["PROJECT_ID"],
        region=os.environ["REGION"],
        agent_engine_id=os.environ["AGENT_ENGINE_ID"],
        copilot_engine_id=os.environ.get("COPILOT_ENGINE_ID", ""),
        # Where card buttons send their clicks: the weave-chat URL. HTTP add-on
        # apps route clicks to onClick.action.function, which must be a URL.
        chat_button_url=os.environ.get("CHAT_BUTTON_URL", ""),
        pubsub_push_sa=os.environ["PUBSUB_PUSH_SA"],
        pubsub_push_audience=os.environ["PUBSUB_PUSH_AUDIENCE"],
        model_armor_input_template=os.environ["MODEL_ARMOR_INPUT_TEMPLATE"],
        artifact_source=os.environ.get("ARTIFACT_SOURCE", "live"),  # type: ignore[arg-type]
        fixture_dir=os.environ.get("FIXTURE_DIR", ""),
        delivery_mode=os.environ.get("DELIVERY_MODE", "chat"),  # type: ignore[arg-type]
        admin_subject=os.environ.get("ADMIN_SUBJECT", ""),
        subscription_job_name=os.environ.get("SUBSCRIPTION_JOB_NAME", ""),
        workspace_timezone=os.environ["WORKSPACE_TIMEZONE"],
    )
