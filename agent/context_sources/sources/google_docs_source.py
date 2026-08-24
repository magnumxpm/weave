"""Owner-scoped Google Drive context via the ingestion broker."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from weave_common import ContextMatch

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.broker_client import fetch_broker_matches
from agent.context_sources.registry import register_source

logger = logging.getLogger(__name__)
BrokerFetch = Callable[[str, str, str, str, str, int], list[ContextMatch]]


@register_source("google_docs", AuthMode.USER_CONTEXT)
class GoogleDocsSource(ContextSource):
    def __init__(
        self,
        base_url: str | None = None,
        audience: str | None = None,
        fetch_fn: BrokerFetch | None = None,
    ) -> None:
        self._base_url = (
            base_url if base_url is not None else os.environ.get("CONTEXT_BROKER_URL", "")
        )
        self._audience = (
            audience if audience is not None else os.environ.get("CONTEXT_BROKER_AUDIENCE", "")
        )
        self._fetch = fetch_fn or fetch_broker_matches

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        if not self._base_url or not self._audience:
            logger.warning("google_docs source disabled: broker not configured")
            return []
        if not query.strip() or limit <= 0:
            return []
        return self._fetch(
            self._base_url,
            self._audience,
            self.name,
            query,
            principal.email,
            limit,
        )
