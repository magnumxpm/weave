from __future__ import annotations

from datetime import date
from typing import Any

from agent.copilot.store_reader import CopilotStoreReader


class Snapshot:
    def __init__(self, document_id: str, data: dict[str, Any] | None) -> None:
        self.id = document_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class Document:
    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    def get(self) -> Snapshot:
        return self.snapshot


class Query:
    def __init__(self, rows: list[Snapshot]) -> None:
        self.rows = rows
        self.filters: list[tuple[str, str, Any]] = []

    def where(self, *, filter: Any) -> Query:
        self.filters.append((filter.field_path, filter.op_string, filter.value))
        return self

    def order_by(self, field: str, *, direction: Any) -> Query:
        del field, direction
        return self

    def limit(self, value: int) -> Query:
        del value
        return self

    def stream(self) -> list[Snapshot]:
        return self.rows


class Collection(Query):
    def __init__(self, rows: list[Snapshot], documents: dict[str, Snapshot]) -> None:
        super().__init__(rows)
        self.documents = documents

    def document(self, document_id: str) -> Document:
        return Document(self.documents.get(document_id, Snapshot(document_id, None)))


class Client:
    def __init__(self) -> None:
        self.queries: dict[str, Collection] = {
            "meeting_summaries": Collection(
                [],
                {
                    "visible": Snapshot(
                        "visible",
                        {"overview": "Visible", "visible_to": ["owner@example.com"]},
                    ),
                    "hidden": Snapshot(
                        "hidden",
                        {"overview": "Hidden", "visible_to": ["other@example.com"]},
                    ),
                },
            ),
            "action_items": Collection([Snapshot("mention", {"description": "Send report"})], {}),
        }

    def collection(self, name: str) -> Collection:
        return self.queries[name]


def test_exact_meeting_summary_is_attendee_guarded() -> None:
    reader = CopilotStoreReader(Client())

    assert reader.get_meeting_summary("owner@example.com", "meeting_summaries/visible")
    assert reader.get_meeting_summary("owner@example.com", "meeting_summaries/hidden") is None


def test_commitment_date_lookup_filters_by_owner_in_firestore() -> None:
    client = Client()
    reader = CopilotStoreReader(client)
    rows = reader.list_commitment_mentions(
        "owner@example.com", date(2026, 8, 20), date(2026, 8, 20)
    )

    assert rows[0]["mention_ref"] == "mention"
    assert client.queries["action_items"].filters[0] == (
        "owner_email",
        "==",
        "owner@example.com",
    )
