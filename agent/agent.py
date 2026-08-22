"""Lazy ADK discovery surface."""

from __future__ import annotations

import os
from typing import Any


def __getattr__(name: str) -> Any:
    if name == "root_agent":
        if os.environ.get("WEAVE_EVAL_AGENT") == "enrichment":
            from agent.evaluation import get_enrichment_eval_agent

            return get_enrichment_eval_agent()
        from agent.agents.extraction import get_extraction_agent

        return get_extraction_agent()
    raise AttributeError(name)
