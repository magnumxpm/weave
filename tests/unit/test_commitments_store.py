from copy import deepcopy
from datetime import date
from typing import Any

from weave_common import (
    CommitmentMention,
    CommitmentState,
    MentionRelationship,
    ReconcileDecision,
)
from weave_ingestion.commitments import CommitmentStore, commitment_id_for


class Snapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._data)


class Document:
    def __init__(self, client: "Client", path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path
        self.id = path[-1]

    def get(self, **kwargs: Any) -> Snapshot:
        return Snapshot(self.client.data.get(self.path))

    def set(self, value: dict[str, Any], merge: bool = False) -> None:
        if merge:
            self.client.data.setdefault(self.path, {}).update(deepcopy(value))
        else:
            self.client.data[self.path] = deepcopy(value)

    def collection(self, name: str) -> "Collection":
        return Collection(self.client, (*self.path, name))


class Collection:
    def __init__(self, client: "Client", path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def document(self, name: str) -> Document:
        return Document(self.client, (*self.path, name))


class Client:
    def __init__(self) -> None:
        self.data: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> Collection:
        return Collection(self, (name,))


def decision(match: str | None = None, title: str = "Launch brief") -> ReconcileDecision:
    return ReconcileDecision(
        matched_commitment_id=match,
        confidence=0.95,
        relationship=(MentionRelationship.CARRIED_OVER if match else MentionRelationship.ORIGINAL),
        canonical_title=title,
        inferred_state=CommitmentState.OPEN,
    )


def mention(reference: str, day: int, summary_ref: str | None = None) -> CommitmentMention:
    return CommitmentMention(
        mention_ref=reference,
        meeting_date=date(2026, 8, day),
        relationship=MentionRelationship.ORIGINAL,
        excerpt="Ship the launch brief",
        meeting_summary_ref=summary_ref,
    )


def test_apply_create_merge_idempotency_and_deadline_max() -> None:
    client = Client()
    store = CommitmentStore(client)
    first = mention("meeting-1--owner@example.com--0", 20)
    commitment_id = store.apply(
        decision(),
        first,
        "Owner@Example.com",
        None,
        deadline=date(2026, 8, 25),
    )
    assert commitment_id == commitment_id_for(first.mention_ref)
    path = ("commitments", commitment_id)
    assert client.data[path]["owner_email"] == "owner@example.com"
    assert client.data[path]["mention_count"] == 1
    assert client.data[("action_items", first.mention_ref)]["commitment_id"] == commitment_id

    # Pub/Sub/backfill replay is a no-op because the mention subdocument exists.
    store.apply(decision(), first, "owner@example.com", None, deadline=date(2026, 9, 1))
    assert client.data[path]["mention_count"] == 1
    assert client.data[path]["deadline"] == "2026-08-25"

    second = mention("meeting-2--owner@example.com--0", 24)
    store.apply(
        decision(commitment_id, "Canonical launch brief"),
        second,
        "owner@example.com",
        None,
        deadline=date(2026, 9, 1),
    )
    assert client.data[path]["mention_count"] == 2
    assert client.data[path]["deadline"] == "2026-09-01"
    assert client.data[path]["title"] == "Canonical launch brief"
    assert client.data[path]["last_mentioned"] == "2026-08-24"
    assert client.data[path]["first_seen"] == "2026-08-20"

    # A backfill walks write order, so a mention older than the one that created
    # the commitment can arrive last. The carry-over span is measured from
    # first_seen, so it has to move earlier rather than stay put.
    earliest = mention("meeting-0--owner@example.com--0", 3)
    store.apply(decision(commitment_id), earliest, "owner@example.com", None)
    assert client.data[path]["first_seen"] == "2026-08-03"
    assert client.data[path]["last_mentioned"] == "2026-08-24"


def test_close_and_reopen_are_owner_guarded_and_idempotent() -> None:
    client = Client()
    store = CommitmentStore(client)
    first = mention("meeting--owner@example.com--0", 20)
    commitment_id = store.apply(decision(), first, "owner@example.com", None)

    assert store.close(commitment_id, "other@example.com", "copilot") is False
    assert client.data[("commitments", commitment_id)]["status"] == "open"
    assert store.close(commitment_id, "owner@example.com", "copilot") is True
    assert store.close(commitment_id, "owner@example.com", "copilot") is True
    assert client.data[("commitments", commitment_id)]["status"] == "closed"
    assert client.data[("commitments", commitment_id)]["closed_by"] == "copilot"

    assert store.reopen(commitment_id, "other@example.com") is False
    assert store.reopen(commitment_id, "owner@example.com") is True
    assert client.data[("commitments", commitment_id)]["status"] == "open"
    assert client.data[("commitments", commitment_id)]["closed_at"] is None


def test_uuid5_is_stable_and_mention_specific() -> None:
    assert commitment_id_for("one") == commitment_id_for("one")
    assert commitment_id_for("one") != commitment_id_for("two")


def test_commitment_and_mentions_retain_summary_provenance() -> None:
    client = Client()
    store = CommitmentStore(client)
    latest = mention("meeting-2--owner@example.com--0", 24, "meeting_summaries/meeting-2")
    commitment_id = store.apply(decision(), latest, "owner@example.com", None)
    earlier = mention("meeting-1--owner@example.com--0", 20, "meeting_summaries/meeting-1")
    store.apply(decision(commitment_id), earlier, "owner@example.com", None)

    commitment = client.data[("commitments", commitment_id)]
    assert commitment["first_meeting_summary_ref"] == "meeting_summaries/meeting-1"
    assert commitment["latest_meeting_summary_ref"] == "meeting_summaries/meeting-2"
    mention_path = ("commitments", commitment_id, "mentions", earlier.mention_ref)
    assert client.data[mention_path]["meeting_summary_ref"] == "meeting_summaries/meeting-1"
