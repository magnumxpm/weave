"""The single model-facing context search tool."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from google.adk.tools.tool_context import ToolContext

import agent.context_sources  # noqa: F401 - imports built-ins for registration
from agent.context_sources.base import ContextSource, SearchPrincipal
from agent.context_sources.registry import build_sources, search_all

logger = logging.getLogger(__name__)
RECALL = 20


@lru_cache(maxsize=1)
def _default_sources() -> tuple[ContextSource, ...]:
    config_path = Path(__file__).parents[1] / "context_sources" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return tuple(build_sources(config))


def _principal_from_state(tool_context: ToolContext) -> SearchPrincipal | None:
    raw = tool_context.state.get("search_principal")
    if raw is None:
        return None
    try:
        return raw if isinstance(raw, SearchPrincipal) else SearchPrincipal.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _own_meeting_id(tool_context: ToolContext) -> str | None:
    """Bare id of the meeting being enriched, if state names one."""
    raw = tool_context.state.get("conference_record_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.rsplit("/", 1)[-1]


def _search(
    query: str, tool_context: ToolContext, sources: Sequence[ContextSource]
) -> list[dict[str, Any]]:
    principal = _principal_from_state(tool_context)
    if principal is None:
        logger.warning("context search skipped: no valid principal")
        return []
    matches = search_all(list(sources), query, principal, limit=RECALL)
    # A meeting is never its own prior context. Nothing from the current
    # meeting is stored yet on a first run, but a reprocessed one would
    # otherwise offer an item its own earlier copy.
    meeting_id = _own_meeting_id(tool_context)
    if meeting_id is not None:
        matches = [
            match
            for match in matches
            if (match.conference_record_id or "").rsplit("/", 1)[-1] != meeting_id
            and not (match.ref or "").startswith(f"{meeting_id}--")
            and match.ref != f"meeting_summaries/{meeting_id}"
        ]
    return [match.model_dump(mode="json") for match in matches]


def make_search_related_context_tool(
    sources: Sequence[ContextSource],
) -> Callable[[str, ToolContext], list[dict[str, Any]]]:
    """Create an isolated tool closure for injected source instances."""

    def injected_search_related_context(
        query: str, tool_context: ToolContext
    ) -> list[dict[str, Any]]:
        """Search owner-visible sources for context related to an action item."""
        return _search(query, tool_context, sources)

    injected_search_related_context.__name__ = "search_related_context"
    return injected_search_related_context


def search_related_context(query: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Search configured sources as the principal in session state."""
    return _search(query, tool_context, _default_sources())
