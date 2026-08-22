from __future__ import annotations

from copy import deepcopy
from typing import Any

from weave_ingestion.firestore_client import MEETINGS, ONBOARDED, MeetingLedger


class Snapshot:
    def __init__(self, document_id: str, data: dict[str, Any]) -> None:
        self.id = document_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)


class Document:
    def __init__(self, document_id: str, documents: dict[str, dict[str, Any]]) -> None:
        self.id = document_id
        self.documents = documents

    def get(self) -> Snapshot:
        return Snapshot(self.id, self.documents.get(self.id, {}))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        if merge:
            self.documents.setdefault(self.id, {}).update(deepcopy(data))
        else:
            self.documents[self.id] = deepcopy(data)

    def delete(self) -> None:
        self.documents.pop(self.id, None)


class Collection:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents

    def document(self, document_id: str) -> Document:
        return Document(document_id, self.documents)

    def stream(self) -> list[Snapshot]:
        return [Snapshot(document_id, data) for document_id, data in self.documents.items()]


class Client:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> Collection:
        return Collection(self.collections.setdefault(name, {}))


def test_active_onboarded_users_are_keyed_by_normalized_email() -> None:
    client = Client()
    client.collections[ONBOARDED] = {
        "1": {"email": "Active@Example.com", "status": "active", "dm_space": "spaces/one"},
        "2": {"email": "gone@example.com", "status": "offboarding"},
        "3": {"status": "active"},
    }
    users = MeetingLedger(client).onboarded_by_email()
    assert list(users) == ["active@example.com"]
    assert users["active@example.com"].user_id == "1"


def test_upsert_preserves_first_onboarded_time_and_reactivates() -> None:
    client = Client()
    client.collections[ONBOARDED] = {
        "123": {
            "email": "old@example.com",
            "status": "offboarding",
            "onboarded_at": "original",
        }
    }
    user = MeetingLedger(client).upsert_onboarded_user(
        user_id="123", email="NEW@EXAMPLE.COM", dm_space="spaces/new"
    )
    stored = client.collections[ONBOARDED]["123"]
    assert user.email == "new@example.com"
    assert stored["status"] == "active"
    assert stored["onboarded_at"] == "original"
    assert stored["dm_space"] == "spaces/new"


def test_offboarding_tombstone_is_deleted_only_explicitly() -> None:
    client = Client()
    ledger = MeetingLedger(client)
    ledger.mark_offboarding(user_id="123", email="user@example.com", dm_space="spaces/dm")
    assert client.collections[ONBOARDED]["123"]["status"] == "offboarding"
    ledger.delete_onboarded_user("123")
    assert client.collections[ONBOARDED] == {}


def test_mark_records_delivery_outcomes_with_firestore_safe_keys() -> None:
    client = Client()
    MeetingLedger(client).mark(
        "meeting",
        "delivered_partial",
        {"Owner.Name@Example.com": "delivery_failed"},
    )
    stored = client.collections[MEETINGS]["meeting"]
    assert stored["deliveries"] == {"owner,name@example,com": "delivery_failed"}
