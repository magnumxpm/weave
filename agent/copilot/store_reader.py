"""Small Firestore reader used by deterministic copilot tools."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from weave_common import rank

from agent.context_sources.base import SearchPrincipal
from agent.context_sources.embeddings import embed_query
from agent.context_sources.sources.meeting_summary_source import MeetingSummarySource
from agent.context_sources.sources.prior_meeting_source import PriorMeetingSource


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items() if key != "embedding"}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _row(snapshot: Any) -> dict[str, Any]:
    return {"commitment_id": snapshot.id, **_json_safe(snapshot.to_dict() or {})}


def _meeting_row(snapshot: Any) -> dict[str, Any]:
    return {
        "meeting_summary_ref": f"meeting_summaries/{snapshot.id}",
        **_json_safe(snapshot.to_dict() or {}),
    }


def _meeting_text(row: dict[str, Any]) -> str:
    parts = [str(row.get("meeting_title") or ""), str(row.get("overview") or "")]
    for key in ("topics", "decisions", "implementation_notes", "reproduction_steps"):
        values = row.get(key) or []
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
    return "\n".join(parts)


class CopilotStoreReader:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._client

    def list_commitments(self, owner: str, status_filter: str = "") -> list[dict[str, Any]]:
        query = self.client.collection("commitments").where(
            filter=FieldFilter("owner_email", "==", owner)
        )
        if status_filter:
            query = query.where(filter=FieldFilter("status", "==", status_filter))
        return [_row(row) for row in query.stream()]

    def get_commitment(self, owner: str, commitment_id: str) -> dict[str, Any] | None:
        collection = self.client.collection("commitments")
        reference = collection.document(commitment_id)
        snapshots = list(
            collection.where(filter=FieldFilter("owner_email", "==", owner))
            .where(filter=FieldFilter("__name__", "==", reference))
            .limit(1)
            .stream()
        )
        if not snapshots:
            return None
        data = _json_safe(snapshots[0].to_dict() or {})
        return {"commitment_id": commitment_id, **data}

    def mention_excerpt(self, commitment_id: str, mention_ref: str) -> str | None:
        snapshot = (
            self.client.collection("commitments")
            .document(commitment_id)
            .collection("mentions")
            .document(mention_ref)
            .get()
        )
        data = snapshot.to_dict() if getattr(snapshot, "exists", True) else None
        excerpt = data.get("excerpt") if data else None
        return excerpt if isinstance(excerpt, str) else None

    def history(self, owner: str, commitment_id: str) -> dict[str, Any] | None:
        commitment = self.get_commitment(owner, commitment_id)
        if commitment is None:
            return None
        mentions = [
            {"mention_ref": row.id, **_json_safe(row.to_dict() or {})}
            for row in self.client.collection("commitments")
            .document(commitment_id)
            .collection("mentions")
            .order_by("meeting_date")
            .stream()
        ]
        return {"commitment": commitment, "mentions": mentions}

    def stale(self, owner: str, days: int) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC).date() - timedelta(days=max(1, min(days, 3650)))
        snapshots = (
            self.client.collection("commitments")
            .where(filter=FieldFilter("owner_email", "==", owner))
            .where(filter=FieldFilter("last_mentioned", "<", cutoff.isoformat()))
            .order_by("last_mentioned")
            .stream()
        )
        return [
            row
            for snapshot in snapshots
            if (row := _row(snapshot)).get("status") in {"open", "waiting"}
        ]

    def search_history(self, owner: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        source = PriorMeetingSource(client=self.client, embed_query_fn=embed_query)
        matches = source.search(query, SearchPrincipal(email=owner), limit=limit)
        return [match.model_dump(mode="json") for match in matches]

    def get_meeting_summary(self, owner: str, reference: str) -> dict[str, Any] | None:
        meeting_id = reference.strip().rsplit("/", 1)[-1]
        if not meeting_id:
            return None
        snapshot = self.client.collection("meeting_summaries").document(meeting_id).get()
        if getattr(snapshot, "exists", True) is False:
            return None
        data = snapshot.to_dict() or {}
        visible_to = {str(email).strip().casefold() for email in data.get("visible_to") or []}
        if owner.strip().casefold() not in visible_to:
            return None
        return _meeting_row(snapshot)

    def list_meeting_summaries(
        self,
        owner: str,
        start: date | None,
        end: date | None,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = self.client.collection("meeting_summaries").where(
            filter=FieldFilter("visible_to", "array_contains", owner)
        )
        if start is not None:
            query = query.where(filter=FieldFilter("meeting_date", ">=", start.isoformat()))
        if end is not None:
            query = query.where(filter=FieldFilter("meeting_date", "<=", end.isoformat()))
        query = query.order_by("meeting_date", direction=firestore.Query.DESCENDING)
        return [_meeting_row(row) for row in query.limit(max(1, min(limit, 40))).stream()]

    def search_meeting_summaries(
        self,
        owner: str,
        query: str,
        start: date | None,
        end: date | None,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(limit, 20))
        if start is not None or end is not None or not query.strip():
            candidates = self.list_meeting_summaries(owner, start, end, limit=40)
            if not query.strip():
                return candidates[:capped]
            texts = [_meeting_text(row) for row in candidates]
            return [candidates[index] for index, _ in rank(query, texts)[:capped]]

        source = MeetingSummarySource(client=self.client, embed_query_fn=embed_query)
        matches = source.search(query, SearchPrincipal(email=owner), limit=capped)
        rows = []
        for match in matches:
            row = self.get_meeting_summary(owner, match.ref or "")
            if row is not None:
                row["relevance_score"] = match.score
                rows.append(row)
        return rows

    def list_commitment_mentions(
        self,
        owner: str,
        start: date | None,
        end: date | None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = self.client.collection("action_items").where(
            filter=FieldFilter("owner_email", "==", owner)
        )
        if start is not None:
            query = query.where(filter=FieldFilter("meeting_date", ">=", start.isoformat()))
        if end is not None:
            query = query.where(filter=FieldFilter("meeting_date", "<=", end.isoformat()))
        query = query.order_by("meeting_date", direction=firestore.Query.DESCENDING)
        return [
            {"mention_ref": row.id, **_json_safe(row.to_dict() or {})}
            for row in query.limit(max(1, min(limit, 200))).stream()
        ]

    def close(self, owner: str, commitment_id: str, closed_by: str = "copilot") -> bool:
        # The owner-guarded read is the authorization check: a commitment this
        # principal cannot see is one they cannot close.
        commitment = self.get_commitment(owner, commitment_id)
        if commitment is None:
            return False
        now = datetime.now(UTC)
        self.client.collection("commitments").document(commitment_id).set(
            {
                "status": "closed",
                "closed_by": closed_by,
                "closed_at": now,
                "updated_at": now,
            },
            merge=True,
        )
        return True

    def reopen(self, owner: str, commitment_id: str) -> bool:
        commitment = self.get_commitment(owner, commitment_id)
        if commitment is None:
            return False
        self.client.collection("commitments").document(commitment_id).set(
            {
                "status": "open",
                "closed_by": None,
                "closed_at": None,
                "updated_at": datetime.now(UTC),
            },
            merge=True,
        )
        return True
