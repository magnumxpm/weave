from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError
from weave_common import (
    ActionItem,
    ActionType,
    Attendee,
    CommitmentStatus,
    EnrichedActionItem,
    EnrichedOwnerBundle,
    MeetingSummaryContent,
    PipelineRequest,
    PipelineResult,
    Reference,
    ReferenceStatus,
    TranscriptTurn,
)
from weave_ingestion.firestore_client import (
    ACTION_ITEMS,
    MEETING_SUMMARIES,
    MEETINGS,
    ONBOARDED,
    MeetingLedger,
)


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

    def batch(self) -> Any:
        operations: list[tuple[Document, dict[str, Any]]] = []

        class Batch:
            def set(self, reference: Document, data: dict[str, Any]) -> None:
                operations.append((reference, data))

            def commit(self) -> None:
                for reference, data in operations:
                    reference.set(data)

        return Batch()


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


def test_upsert_rejects_an_unusable_email_without_writing() -> None:
    # A record that fails validation would be skipped by every later read, so
    # the user would look onboarded and silently receive nothing.
    client = Client()
    with pytest.raises(ValidationError):
        MeetingLedger(client).upsert_onboarded_user(user_id="123", email="", dm_space="spaces/dm")
    assert client.collections.get(ONBOARDED, {}) == {}


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


def test_action_item_persistence_includes_reference_provenance() -> None:
    client = Client()
    embedded_texts: list[str] = []

    def embed(texts: Any) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[0.25] * 768 for _ in texts]

    item = ActionItem(
        description="Follow up with Srija Ghosh",
        source_text="follow up with me",
        references=[
            Reference(
                mention="me",
                turn_ref=4,
                status=ReferenceStatus.RESOLVED,
                email="srija@example.com",
                display_name="Srija Ghosh",
                confidence=1.0,
            )
        ],
        action_type=ActionType.FOLLOW_UP,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1.0,
        resolution_turn_ref=5,
    )
    bundle = EnrichedOwnerBundle(
        owner_email="owner@example.com",
        conference_record_id="conferenceRecords/one",
        meeting_date=date(2026, 8, 23),
        items=[
            EnrichedActionItem(
                item=item,
                title="Resolve Srija's support request",
                details="Check the open device ticket and follow up.",
            )
        ],
        enriched=True,
    )

    MeetingLedger(client, embed).write_action_items("one", [bundle], ["srija@example.com"])

    stored = next(iter(client.collections[ACTION_ITEMS].values()))
    assert stored["source_text"] == "follow up with me"
    assert stored["references"] == [
        {
            "mention": "me",
            "turn_ref": 4,
            "status": "resolved",
            "email": "srija@example.com",
            "display_name": "Srija Ghosh",
            "confidence": 1.0,
        }
    ]
    assert stored["title"] == "Resolve Srija's support request"
    assert stored["details"] == "Check the open device ticket and follow up."
    assert stored["meeting_date"] == "2026-08-23"
    assert len(stored["embedding"]) == 768
    assert embedded_texts == [
        "Resolve Srija's support request\nCheck the open device ticket and follow up."
    ]


def test_action_item_embedding_failure_never_costs_the_history_write() -> None:
    client = Client()
    item = ActionItem(
        description="Send the report",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1.0,
        resolution_turn_ref=2,
    )
    bundle = EnrichedOwnerBundle(
        owner_email="owner@example.com",
        conference_record_id="conferenceRecords/one",
        meeting_date=date(2026, 8, 23),
        items=[EnrichedActionItem(item=item)],
        enriched=True,
    )

    def fail(_: Any) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")

    MeetingLedger(client, fail).write_action_items("one", [bundle], ["owner@example.com"])

    stored = next(iter(client.collections[ACTION_ITEMS].values()))
    assert stored["description"] == "Send the report"
    assert "embedding" not in stored


def test_persist_meeting_writes_summary_and_cross_references_atomically() -> None:
    client = Client()
    item = ActionItem(
        description="Reproduce the login failure, then update the token handler",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=1.0,
        resolution_turn_ref=2,
    )
    bundle = EnrichedOwnerBundle(
        owner_email="owner@example.com",
        conference_record_id="conferenceRecords/one",
        meeting_date=date(2026, 8, 23),
        items=[EnrichedActionItem(item=item, details="Use the three recorded repro steps.")],
        enriched=True,
    )
    summary = MeetingSummaryContent(
        overview="The team diagnosed the login failure.",
        topics=["Authentication"],
        implementation_notes=["Refresh the token before retrying."],
        reproduction_steps=["Expire the token.", "Open the app.", "Retry login."],
    )
    request = PipelineRequest(
        transcript_turns=[
            TranscriptTurn(turn_index=0, participant_id="p1", speaker_name="Owner", text="Login")
        ],
        conference_record_id="conferenceRecords/one",
        meeting_date=date(2026, 8, 23),
        attendees=[Attendee(email="Owner@Example.com", participant_id="p1", display_name="Owner")],
        meeting_title="Authentication review",
    )
    result = PipelineResult(
        conference_record_id=request.conference_record_id,
        summary=summary,
        bundles=[bundle],
        dropped_item_count=0,
    )

    rows = MeetingLedger(client, lambda texts: [[0.2] * 768 for _ in texts]).persist_meeting(
        "one", request, result, ["Owner@Example.com"]
    )

    action = next(iter(client.collections[ACTION_ITEMS].values()))
    stored_summary = client.collections[MEETING_SUMMARIES]["one"]
    assert action["meeting_summary_ref"] == "meeting_summaries/one"
    assert rows[0].meeting_summary_ref == "meeting_summaries/one"
    assert stored_summary["overview"] == summary.overview
    assert stored_summary["reproduction_steps"] == summary.reproduction_steps
    assert stored_summary["visible_to"] == ["owner@example.com"]
    assert len(stored_summary["embedding"]) == 768


def test_persist_meeting_stores_a_summary_even_without_action_items() -> None:
    client = Client()
    request = PipelineRequest(
        transcript_turns=[],
        conference_record_id="conferenceRecords/quiet",
        meeting_date=date(2026, 8, 23),
        attendees=[],
    )
    result = PipelineResult(
        conference_record_id=request.conference_record_id,
        summary=MeetingSummaryContent(overview="A discussion with no commitments."),
        bundles=[],
        dropped_item_count=0,
    )

    rows = MeetingLedger(client, lambda texts: [[0.2] * 768 for _ in texts]).persist_meeting(
        "quiet", request, result, []
    )

    assert rows == []
    assert client.collections[MEETING_SUMMARIES]["quiet"]["overview"].startswith("A discussion")


def test_summary_embedding_failure_keeps_lexical_summary() -> None:
    client = Client()
    request = PipelineRequest(
        transcript_turns=[],
        conference_record_id="conferenceRecords/lexical",
        meeting_date=date(2026, 8, 23),
        attendees=[],
    )
    result = PipelineResult(
        conference_record_id=request.conference_record_id,
        summary=MeetingSummaryContent(overview="Searchable without an embedding."),
        bundles=[],
        dropped_item_count=0,
    )

    def fail(_: Any) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")

    MeetingLedger(client, fail).persist_meeting("lexical", request, result, [])

    stored = client.collections[MEETING_SUMMARIES]["lexical"]
    assert stored["overview"] == "Searchable without an embedding."
    assert "embedding" not in stored
