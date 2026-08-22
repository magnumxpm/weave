"""Read prior action items using the principal ACL in the Firestore query."""

from __future__ import annotations

from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from weave_common import ContextMatch, MatchType

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.registry import register_source


@register_source("prior_meetings", AuthMode.USER_CONTEXT)
class PriorMeetingSource(ContextSource):
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = firestore.Client()
        return self._client

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        del query  # v1 intentionally uses recency; semantic search is not claimed.
        snapshots = (
            self.client.collection("action_items")
            .where(filter=FieldFilter("visible_to", "array_contains", principal.email))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        matches: list[ContextMatch] = []
        for snapshot in snapshots:
            data = snapshot.to_dict()
            description = str(data.get("description", "Prior action item"))
            matches.append(
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.EXISTING_PRIOR_ITEM,
                    title=str(data.get("title", description)),
                    snippet=str(data.get("snippet", description)),
                    ref=getattr(snapshot, "id", None),
                    score=None,
                )
            )
        return matches
