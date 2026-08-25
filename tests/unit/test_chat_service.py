import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from weave_chat.config import ChatSettings
from weave_chat.main import create_app
from weave_ingestion.oidc import PushAuthError

SETTINGS = ChatSettings(
    project_id="weave-test",
    chat_audience="https://weave-chat.example.run.app",
    chat_events_topic="projects/weave-test/topics/chat-events",
)
AUTH = {"Authorization": "Bearer chat-token"}
OWNER = "owner@example.com"


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._store.get(self._key))


class FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> FakeDocument:
        return FakeDocument(self._store, key)


class FakeClient:
    def __init__(self, commitments: dict[str, dict[str, Any]]) -> None:
        self._commitments = commitments

    def collection(self, name: str) -> FakeCollection:
        assert name == "commitments"
        return FakeCollection(self._commitments)


class FakeStore:
    """Stands in for CommitmentStore; records what a click actually mutated."""

    def __init__(self, commitments: dict[str, dict[str, Any]] | None = None) -> None:
        self.commitments = commitments or {}
        self.closed: list[tuple[str, str]] = []
        self.reopened: list[tuple[str, str]] = []
        self.client = FakeClient(self.commitments)

    def close(self, commitment_id: str, owner_email: str, closed_by: str = "") -> bool:
        row = self.commitments.get(commitment_id)
        if not row or row.get("owner_email") != owner_email:
            return False
        self.closed.append((commitment_id, owner_email))
        row["status"] = "closed"
        return True

    def reopen(self, commitment_id: str, owner_email: str) -> bool:
        row = self.commitments.get(commitment_id)
        if not row or row.get("owner_email") != owner_email:
            return False
        self.reopened.append((commitment_id, owner_email))
        row["status"] = "open"
        return True

    def commitment_for_mention(self, mention_ref: str) -> str | None:
        for key, row in self.commitments.items():
            if row.get("created_from") == mention_ref:
                return key
        return None


class FakeOnboarded:
    def __init__(self, users: dict[str, str] | None = None) -> None:
        self._users = users if users is not None else {"1234567890": OWNER}

    def email_for(self, user_id: str) -> str | None:
        return self._users.get(user_id)


def commitment(commitment_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "commitment_id": commitment_id,
        "owner_email": OWNER,
        "title": f"Commitment {commitment_id}",
        "status": "open",
        "mention_count": 1,
        "first_seen": "2026-08-20",
        "last_mentioned": "2026-08-24",
        "blocked_by": [],
    } | overrides


def build(
    commitments: dict[str, dict[str, Any]] | None = None,
    users: dict[str, str] | None = None,
    verifier: Any = None,
) -> tuple[TestClient, FakeStore, list[bytes]]:
    store = FakeStore(commitments)
    published: list[bytes] = []
    app = create_app(
        SETTINGS,
        token_verifier=verifier or (lambda token: {"email": "chat@system.gserviceaccount.com"}),
        store=store,  # type: ignore[arg-type]
        onboarded=FakeOnboarded(users),  # type: ignore[arg-type]
        publisher=published.append,
    )
    return TestClient(app), store, published


def click(function: str, **parameters: str) -> dict[str, Any]:
    return {
        "type": "CARD_CLICKED",
        "user": {"name": "users/1234567890", "type": "HUMAN"},
        "action": {
            "actionMethodName": function,
            "parameters": [{"key": key, "value": value} for key, value in parameters.items()],
        },
    }


def message_event(text: str = "what needs my attention?") -> dict[str, Any]:
    return {
        "type": "MESSAGE",
        "user": {"name": "users/1234567890", "type": "HUMAN", "email": OWNER},
        "space": {"name": "spaces/abc", "spaceType": "DIRECT_MESSAGE", "singleUserBotDm": True},
        "message": {"name": "spaces/abc/messages/1", "text": text},
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic nope"}])
def test_requests_without_a_chat_bearer_are_refused(headers: dict[str, str]) -> None:
    client, store, published = build()
    assert client.post("/", json=message_event(), headers=headers).status_code == 401
    assert published == []


def test_a_token_from_anyone_but_chat_is_refused() -> None:
    def reject(token: str) -> dict[str, Any]:
        raise PushAuthError("unexpected sender")

    client, _, published = build(verifier=reject)
    assert client.post("/", json=message_event(), headers=AUTH).status_code == 401
    assert published == []


def test_a_message_is_republished_byte_for_byte_and_answered_immediately() -> None:
    """Ingestion re-parses this payload, so it must arrive exactly as Chat sent it."""
    client, store, published = build()
    raw = json.dumps(message_event()).encode()

    response = client.post("/", content=raw, headers={**AUTH, "Content-Type": "application/json"})

    assert response.status_code == 200
    assert response.json() == {}
    assert published == [raw]
    assert store.closed == []


def test_a_click_closes_the_commitment_and_redraws_the_card_it_was_clicked_from() -> None:
    client, store, _ = build({"c1": commitment("c1"), "c2": commitment("c2")})

    response = client.post(
        "/",
        json=click("close_commitment", commitment_id="c1", rendered_ids="c1,c2"),
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert store.closed == [("c1", OWNER)]
    assert body["actionResponse"]["type"] == "UPDATE_MESSAGE"
    headers = [section.get("header") for section in body["cardsV2"][0]["card"]["sections"]]
    assert "Closed" in headers  # the closed item is redrawn in its new state


def test_a_click_on_someone_elses_commitment_changes_nothing() -> None:
    client, store, _ = build({"theirs": commitment("theirs", owner_email="other@example.com")})

    response = client.post(
        "/", json=click("close_commitment", commitment_id="theirs"), headers=AUTH
    )

    assert response.status_code == 200
    assert store.closed == []
    assert "isn't yours" in response.json()["text"]


def test_an_unknown_clicker_is_told_so_rather_than_guessed_at() -> None:
    client, store, _ = build({"c1": commitment("c1")}, users={})

    response = client.post("/", json=click("close_commitment", commitment_id="c1"), headers=AUTH)

    assert store.closed == []
    assert "onboarded" in response.json()["text"]


def test_a_meeting_card_click_maps_its_one_based_index_onto_the_stored_mention() -> None:
    client, store, _ = build({"c1": commitment("c1", created_from=f"conf-1--{OWNER}--0")})

    response = client.post(
        "/", json=click("mark_done", conference_id="conf-1", item_index="1"), headers=AUTH
    )

    assert store.closed == [("c1", OWNER)]
    assert response.status_code == 200


def test_legacy_buttons_answer_instead_of_going_silent() -> None:
    """Silence is what Chat renders as "unable to process your request"."""
    client, store, _ = build()

    for function in ("accept_item", "decline_item"):
        body = client.post(
            "/", json=click(function, conference_id="conf-1", item_index="1"), headers=AUTH
        ).json()
        assert body["text"].strip()
        assert body["actionResponse"]["type"] == "NEW_MESSAGE"
    assert store.closed == []


def test_an_addon_envelope_gets_the_addon_response_dialect() -> None:
    client, store, _ = build({"c1": commitment("c1")})
    payload = {
        "chat": {
            "user": {"name": "users/1234567890", "type": "HUMAN"},
            "buttonClickedPayload": {
                "action": {
                    "function": "close_commitment",
                    "parameters": [{"key": "commitment_id", "value": "c1"}],
                }
            },
        }
    }

    body = client.post("/", json=payload, headers=AUTH).json()

    assert store.closed == [("c1", OWNER)]
    assert "hostAppDataAction" in body


def test_a_click_whose_card_cannot_be_redrawn_still_confirms_in_words() -> None:
    client, store, _ = build({"c1": commitment("c1")})

    body = client.post("/", json=click("close_commitment", commitment_id="c1"), headers=AUTH).json()

    assert store.closed == [("c1", OWNER)]
    assert "Done" in body["text"]


def test_a_failure_answers_rather_than_returning_an_empty_body() -> None:
    class Exploding(FakeStore):
        def close(self, commitment_id: str, owner_email: str, closed_by: str = "") -> bool:
            raise RuntimeError("firestore down")

    store = Exploding({"c1": commitment("c1")})
    app = create_app(
        SETTINGS,
        token_verifier=lambda token: {},
        store=store,  # type: ignore[arg-type]
        onboarded=FakeOnboarded(),  # type: ignore[arg-type]
        publisher=lambda payload: None,
    )
    body = (
        TestClient(app)
        .post("/", json=click("close_commitment", commitment_id="c1"), headers=AUTH)
        .json()
    )

    assert body["text"].strip()


def test_malformed_payloads_are_acked_not_retried() -> None:
    client, _, published = build()
    response = client.post(
        "/", content=b"not json", headers={**AUTH, "Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert published == []


def test_health_needs_no_token() -> None:
    """Not /healthz: Google's edge answers that path before the container does."""
    client, _, _ = build()
    assert client.get("/health").json() == {"status": "ok"}


def test_base64_helper_is_unused_here_but_payloads_stay_bytes() -> None:
    """Guard the republish contract: ingestion base64-decodes message.data."""
    client, _, published = build()
    raw = json.dumps(message_event()).encode()
    client.post("/", content=raw, headers={**AUTH, "Content-Type": "application/json"})
    assert base64.b64decode(base64.b64encode(published[0])) == raw


def test_a_rejection_records_what_the_token_actually_claimed(caplog: Any) -> None:
    """A wrong audience and an unexpected signer both look like 401 from outside."""
    import base64
    import json
    import logging

    from weave_chat.jwt_auth import describe_token

    claims = {"aud": "884578202776", "iss": "https://accounts.google.com", "email": "x@y.com"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"header.{payload}.signature"

    assert describe_token(token)["aud"] == "884578202776"
    assert describe_token("not-a-jwt") == {"unparsed": True}

    def reject(_: str) -> dict[str, Any]:
        raise PushAuthError("signature/audience check failed")

    client, _, _ = build(verifier=reject)
    with caplog.at_level(logging.WARNING):
        client.post("/", json=message_event(), headers={"Authorization": f"Bearer {token}"})
    assert any("rejected Chat caller" in record.message for record in caplog.records)
