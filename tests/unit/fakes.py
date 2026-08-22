from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeSnapshot:
    id: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.data)


class FakeQuery:
    def __init__(self, documents: list[FakeSnapshot]) -> None:
        self.documents = documents
        self.principal: str | None = None
        self.max_results = 5

    def where(self, *, filter: Any) -> FakeQuery:
        assert filter.field_path == "visible_to"
        assert filter.op_string == "array_contains"
        self.principal = filter.value
        return self

    def order_by(self, field: str, *, direction: Any) -> FakeQuery:
        del direction
        assert field == "created_at"
        return self

    def limit(self, value: int) -> FakeQuery:
        self.max_results = value
        return self

    def stream(self) -> list[FakeSnapshot]:
        visible = [
            document
            for document in self.documents
            if self.principal in document.data.get("visible_to", [])
        ]
        visible.sort(key=lambda document: document.data["created_at"], reverse=True)
        return visible[: self.max_results]


class FakeFirestoreClient:
    def __init__(self, documents: list[FakeSnapshot]) -> None:
        self.documents = documents
        self.collection_name: str | None = None

    def collection(self, name: str) -> FakeQuery:
        self.collection_name = name
        return FakeQuery(self.documents)
