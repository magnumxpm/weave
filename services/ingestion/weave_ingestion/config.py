"""Environment-driven configuration, validated before the app serves traffic."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    region: str
    agent_engine_id: str
    pubsub_push_sa: str
    pubsub_push_audience: str
    model_armor_input_template: str
    artifact_source: Literal["live", "fixture"]
    fixture_dir: str = ""
    delivery_mode: Literal["chat", "log"]
    workspace_subject: str = ""  # user the delegated Meet fetch impersonates (live only)

    @model_validator(mode="after")
    def source_specific_requirements(self) -> Settings:
        if self.artifact_source == "fixture" and not self.fixture_dir:
            raise ValueError("fixture_dir is required when artifact_source=fixture")
        if self.artifact_source == "live" and not self.workspace_subject:
            raise ValueError("workspace_subject is required when artifact_source=live")
        # Chat delivery resolves owner ids through the directory, which is a
        # delegated call and therefore needs a subject to impersonate.
        if self.delivery_mode == "chat" and not self.workspace_subject:
            raise ValueError("workspace_subject is required when delivery_mode=chat")
        return self


def settings_from_env() -> Settings:
    return Settings(
        project_id=os.environ["PROJECT_ID"],
        region=os.environ["REGION"],
        agent_engine_id=os.environ["AGENT_ENGINE_ID"],
        pubsub_push_sa=os.environ["PUBSUB_PUSH_SA"],
        pubsub_push_audience=os.environ["PUBSUB_PUSH_AUDIENCE"],
        model_armor_input_template=os.environ["MODEL_ARMOR_INPUT_TEMPLATE"],
        artifact_source=os.environ.get("ARTIFACT_SOURCE", "live"),  # type: ignore[arg-type]
        fixture_dir=os.environ.get("FIXTURE_DIR", ""),
        delivery_mode=os.environ.get("DELIVERY_MODE", "chat"),  # type: ignore[arg-type]
        workspace_subject=os.environ.get("WORKSPACE_SUBJECT", ""),
    )
