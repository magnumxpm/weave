"""ADK definition for the separately deployed interactive copilot."""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agent.callbacks import make_screen_output_callback
from agent.copilot.principal import seed_copilot_principal
from agent.copilot.prompt import COPILOT_INSTRUCTION
from agent.copilot.tools import (
    close_commitment,
    find_stale_commitments,
    get_commitment_history,
    list_my_commitments,
    reopen_commitment,
    search_my_history,
    search_workspace_evidence,
    trace_blockers,
)


def build_copilot() -> LlmAgent:
    callback = None
    if template := os.environ.get("MODEL_ARMOR_OUTPUT_TEMPLATE"):
        callback = make_screen_output_callback(
            template, os.environ.get("MODEL_ARMOR_LOCATION", "us-central1")
        )
    return LlmAgent(
        name="weave_commitment_copilot",
        model=os.environ.get("WEAVE_MODEL", "gemini-2.5-flash"),
        instruction=COPILOT_INSTRUCTION,
        tools=[
            list_my_commitments,
            get_commitment_history,
            find_stale_commitments,
            trace_blockers,
            search_my_history,
            search_workspace_evidence,
            close_commitment,
            reopen_commitment,
        ],
        before_agent_callback=seed_copilot_principal,
        after_model_callback=callback,
    )
