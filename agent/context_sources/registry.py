"""Fail-closed registration and fan-out for context sources."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, TypeVar

from weave_common import ContextMatch

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal

logger = logging.getLogger(__name__)

SourceType = TypeVar("SourceType", bound=type[ContextSource])
_REGISTRY: dict[str, type[ContextSource]] = {}


def register_source(name: str, auth_mode: AuthMode):
    """Register a source class and bind its declared security mode."""

    def decorator(source_type: SourceType) -> SourceType:
        if name in _REGISTRY and _REGISTRY[name] is not source_type:
            raise ValueError(f"context source already registered: {name}")
        source_type.name = name
        source_type.auth_mode = auth_mode
        _REGISTRY[name] = source_type
        return source_type

    return decorator


def build_sources(
    config: Mapping[str, Any],
    *,
    allow_service_only: bool = False,
    source_kwargs: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[ContextSource]:
    """Build enabled sources, rejecting unknown names before any query runs."""
    sources: list[ContextSource] = []
    for entry in config.get("sources", []):
        name = entry.get("name")
        if name not in _REGISTRY:
            raise ValueError(f"unknown context source: {name}")
        if not entry.get("enabled", True):
            continue
        source_type = _REGISTRY[name]
        if source_type.auth_mode is AuthMode.SERVICE_ONLY and not allow_service_only:
            logger.warning("dropping service-only context source", extra={"source": name})
            continue
        kwargs = dict((source_kwargs or {}).get(name, {}))
        sources.append(source_type(**kwargs))
    return sources


def search_all(
    sources: list[ContextSource],
    query: str,
    principal: SearchPrincipal,
    *,
    limit: int = 5,
) -> list[ContextMatch]:
    results: list[ContextMatch] = []
    for source in sources:
        try:
            results.extend(source.search(query, principal, limit=limit))
        except Exception:  # noqa: BLE001 - source failures intentionally degrade to no results
            logger.exception("context source failed", extra={"source": source.name})
    return results
