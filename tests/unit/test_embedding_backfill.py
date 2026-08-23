from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from scripts.backfill_embeddings import backfill


class Reference:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def update(self, values: dict[str, Any]) -> None:
        self.data.update(deepcopy(values))


class Snapshot:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.reference = Reference(data)

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.data)


class Collection:
    def __init__(self, snapshots: list[Snapshot]) -> None:
        self.snapshots = snapshots

    def stream(self) -> list[Snapshot]:
        return self.snapshots


class Client:
    def __init__(self, snapshots: list[Snapshot]) -> None:
        self.snapshots = snapshots

    def collection(self, name: str) -> Collection:
        assert name == "action_items"
        return Collection(self.snapshots)


def test_backfill_is_idempotent_and_uses_written_text() -> None:
    existing = Snapshot({"description": "Already done", "embedding": "existing"})
    missing = Snapshot(
        {
            "description": "Raw description",
            "title": "Written title",
            "details": "Written details",
        }
    )
    texts: list[str] = []

    def embed(values: Any) -> list[list[float]]:
        texts.extend(values)
        return [[0.5] * 768 for _ in values]

    client = Client([existing, missing])
    assert backfill(client, embed) == 1
    assert texts == ["Written title\nWritten details"]
    assert len(missing.data["embedding"]) == 768

    assert backfill(client, embed) == 0
    assert texts == ["Written title\nWritten details"]


def test_backfill_dates_legacy_items_from_their_write_time() -> None:
    # Documents written before meeting_date existed would otherwise reach the
    # agent undated, leaving staleness unjudgeable.
    legacy = Snapshot(
        {"description": "Older item", "created_at": datetime(2026, 8, 23, 1, 46, tzinfo=UTC)}
    )
    dated = Snapshot(
        {
            "description": "Newer item",
            "meeting_date": "2026-08-20",
            "created_at": datetime(2026, 8, 23, 1, 46, tzinfo=UTC),
        }
    )

    backfill(Client([legacy, dated]), lambda values: [[0.5] * 768 for _ in values])

    assert legacy.data["meeting_date"] == "2026-08-23"
    assert dated.data["meeting_date"] == "2026-08-20"
