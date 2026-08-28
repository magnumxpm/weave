"""Read prior meeting summaries with the attendee ACL in every query."""

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
from weave_common import ContextMatch, MatchType, rank

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.embeddings import embed_query
from agent.context_sources.registry import register_source

logger = logging.getLogger(__name__)
CANDIDATE_WINDOW = 40
LEXICAL_CAP = 5
SNIPPET_LIMIT = 1200


def _summary_text(record: dict[str, Any]) -> str:
    parts = [str(record.get("overview") or "")]
    for key in ("topics", "decisions", "implementation_notes", "reproduction_steps"):
        values = record.get(key) or []
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
    return "\n".join(part.strip() for part in parts if part.strip())


def _date(value: Any) -> date | None:
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


@register_source("meeting_summaries", AuthMode.USER_CONTEXT)
class MeetingSummarySource(ContextSource):
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
            self._client = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._client

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        if not query.strip() or limit <= 0:
            return []
        try:
            matches = self._semantic(query, principal, limit)
            logger.info(
                "meeting-summary search served by vector retrieval",
                extra={"result_count": len(matches)},
            )
            return matches
        except Exception as error:  # noqa: BLE001 - lexical search is the availability fallback
            logger.warning(
                "meeting-summary vector search failed; falling back to lexical",
                extra={"error_type": type(error).__name__},
            )
            return self._lexical(query, principal, limit)

    @staticmethod
    def _match(snapshot: Any, record: dict[str, Any], score: float | None) -> ContextMatch:
        conference_id = str(record.get("conference_record_id") or snapshot.id)
        overview = _summary_text(record) or "Meeting summary"
        return ContextMatch(
            source_name="meeting_summaries",
            match_type=MatchType.MEETING_SUMMARY,
            title=str(record.get("meeting_title") or "Meeting summary"),
            snippet=overview[:SNIPPET_LIMIT],
            ref=f"meeting_summaries/{snapshot.id}",
            score=score,
            occurred_on=_date(record.get("meeting_date")),
            conference_record_id=conference_id,
        )

    def _semantic(self, query: str, principal: SearchPrincipal, limit: int) -> list[ContextMatch]:
        query_vector = self._embed_query(query)
        if not query_vector:
            raise ValueError("query embedding is empty")
        snapshots = (
            self.client.collection("meeting_summaries")
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
        results = []
        for snapshot in snapshots:
            record = snapshot.to_dict() or {}
            distance = float(record.get("vector_distance", 1.0))
            results.append(self._match(snapshot, record, max(0.0, min(1.0, 1 - distance))))
        return results

    def _lexical(self, query: str, principal: SearchPrincipal, limit: int) -> list[ContextMatch]:
        snapshots = list(
            self.client.collection("meeting_summaries")
            .where(filter=FieldFilter("visible_to", "array_contains", principal.email))
            .order_by("meeting_date", direction=firestore.Query.DESCENDING)
            .limit(CANDIDATE_WINDOW)
            .stream()
        )
        records = [snapshot.to_dict() or {} for snapshot in snapshots]
        texts = [_summary_text(record) for record in records]
        return [
            self._match(snapshots[index], records[index], round(score, 4))
            for index, score in rank(query, texts)[: min(limit, LEXICAL_CAP)]
        ]
