"""Read prior action items using the principal ACL in the Firestore query."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from weave_common import ContextMatch, MatchType

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.embeddings import embed_query
from agent.context_sources.registry import register_source
from agent.context_sources.relevance import rank

logger = logging.getLogger(__name__)

# Ranking needs candidates to rank. Recency alone decides what enters the
# window; relevance decides what leaves it.
CANDIDATE_WINDOW = 40
LEXICAL_CAP = 5


@register_source("prior_meetings", AuthMode.USER_CONTEXT)
class PriorMeetingSource(ContextSource):
    def __init__(
        self,
        client: Any | None = None,
        embed_query_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._client = client
        self._embed_query = embed_query_fn or embed_query

    @property
    def client(self) -> Any:
        if self._client is None:
            # Agent Engine's ADC resolves the project *number*, which Firestore
            # rejects with "the database (default) does not exist"; every search
            # then failed into an empty result. The deploy passes the id.
            self._client = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._client

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        if not query.strip() or limit <= 0:
            return []
        try:
            matches = self._semantic(query, principal, limit)
            logger.info("prior-meeting search served by vector retrieval")
            return matches
        except Exception:  # noqa: BLE001 - the established lexical path is the fallback
            logger.exception("vector search failed; falling back to lexical ranking")
            return self._lexical(query, principal, limit)

    @staticmethod
    def _occurred_on(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _semantic(self, query: str, principal: SearchPrincipal, limit: int) -> list[ContextMatch]:
        query_vector = self._embed_query(query)
        if not query_vector:
            return []
        snapshots = list(
            self.client.collection("action_items")
            .where(filter=FieldFilter("visible_to", "array_contains", principal.email))
            .find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_vector),
                distance_measure=DistanceMeasure.COSINE,
                limit=limit,
                distance_result_field="vector_distance",
            )
            .stream()
        )
        matches = []
        for snapshot in snapshots:
            record = snapshot.to_dict() or {}
            description = str(record.get("description") or "Prior action item")
            distance = float(record.get("vector_distance", 1.0))
            matches.append(
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.EXISTING_PRIOR_ITEM,
                    title=str(record.get("title") or description),
                    snippet=description,
                    ref=getattr(snapshot, "id", None),
                    score=max(0.0, min(1.0, 1.0 - distance)),
                    occurred_on=self._occurred_on(record.get("meeting_date")),
                )
            )
        return matches

    def _lexical(self, query: str, principal: SearchPrincipal, limit: int) -> list[ContextMatch]:
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
        for index, score in rank(query, descriptions)[: min(limit, LEXICAL_CAP)]:
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
                    occurred_on=self._occurred_on(record.get("meeting_date")),
                )
            )
        return matches
