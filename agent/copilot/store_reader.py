"""Small Firestore reader used by deterministic copilot tools."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from agent.context_sources.base import SearchPrincipal
from agent.context_sources.embeddings import embed_query
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
