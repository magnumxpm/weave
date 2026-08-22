"""Weave ADK package with a lazy extraction-only root agent."""

from __future__ import annotations

import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    if name == "agent":
        return importlib.import_module("agent.agent")
    if name == "root_agent":
        from agent.agents.extraction import get_extraction_agent

        return get_extraction_agent()
    raise AttributeError(name)
