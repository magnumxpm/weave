from __future__ import annotations

from copy import deepcopy
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
