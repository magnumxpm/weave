"""Read prior action items using the principal ACL in the Firestore query."""

from __future__ import annotations

import os
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from weave_common import ContextMatch, MatchType

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.registry import register_source
from agent.context_sources.relevance import rank

# Ranking needs candidates to rank. Recency alone decides what enters the
# window; relevance decides what leaves it.
CANDIDATE_WINDOW = 40


@register_source("prior_meetings", AuthMode.USER_CONTEXT)
class PriorMeetingSource(ContextSource):
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            # Agent Engine's ADC resolves the project *number*, which Firestore
            # rejects with "the database (default) does not exist"; every search
            # then failed into an empty result. The deploy passes the id.
            self._client = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._client

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        snapshots = list(
            self.client.collection("action_items")
            .where(filter=FieldFilter("visible_to", "array_contains", principal.email))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(CANDIDATE_WINDOW)
            .stream()
        )
        records = [snapshot.to_dict() or {} for snapshot in snapshots]
        descriptions = [str(record.get("description", "")) for record in records]

        matches: list[ContextMatch] = []
        for index, score in rank(query, descriptions)[:limit]:
            record = records[index]
            description = descriptions[index] or "Prior action item"
            matches.append(
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.EXISTING_PRIOR_ITEM,
                    title=str(record.get("title", description)),
                    snippet=str(record.get("snippet", description)),
                    ref=getattr(snapshots[index], "id", None),
                    score=round(score, 4),
                )
            )
        return matches
