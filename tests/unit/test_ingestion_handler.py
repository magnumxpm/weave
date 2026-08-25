from __future__ import annotations

import base64
import json
import logging
from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from weave_common import (
    ActionItem,
    ActionType,
    Attendee,
    CommitmentStatus,
    ContextMatch,
    EnrichedActionItem,
    EnrichedOwnerBundle,
    MatchType,
    PipelineRequest,
    PipelineResult,
    TranscriptTurn,
)
from weave_ingestion.config import Settings
from weave_ingestion.delivery.base import Deliverer, MeetingHeader
from weave_ingestion.firestore_client import OnboardedUser
from weave_ingestion.main import create_app
from weave_ingestion.meet_client import (
    MeetArtifactSource,
    extract_conference_id,
    extract_subscriber_user_id,
)
from weave_ingestion.oidc import PushAuthError


def settings() -> Settings:
    return Settings(
        project_id="test-project",
        region="us-central1",
        agent_engine_id="projects/p/locations/l/reasoningEngines/1",
        pubsub_push_sa="push@test.iam.gserviceaccount.com",
        pubsub_push_audience="weave-ingestion",
        model_armor_input_template="projects/p/locations/l/templates/t",
        artifact_source="fixture",
        fixture_dir="/tmp/unused",
        delivery_mode="log",
    )


def pipeline_request() -> PipelineRequest:
    return PipelineRequest(
        transcript_turns=[
            TranscriptTurn(turn_index=0, participant_id="p1", speaker_name="Ana", text="hello")
        ],
        conference_record_id="conferenceRecords/abc123",
        meeting_date=date(2026, 8, 22),
        attendees=[
            Attendee(email="ana@example.com", participant_id="p1", display_name="Ana"),
            Attendee(email="bob@example.com", participant_id="p2", display_name="Bob"),
        ],
    )


def pipeline_result() -> PipelineResult:
    item = ActionItem(
        description="Send report",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="ana@example.com",
        owner_confidence=0.95,
        commitment_turn_ref=0,
        resolution_turn_ref=0,
    )
    return PipelineResult(
        conference_record_id="conferenceRecords/abc123",
        bundles=[
            EnrichedOwnerBundle(
                owner_email="ana@example.com",
                conference_record_id="conferenceRecords/abc123",
                meeting_date=date(2026, 8, 22),
                items=[EnrichedActionItem(item=item)],
                enriched=True,
            )
        ],
        dropped_item_count=1,
    )


def two_owner_result() -> PipelineResult:
    first = pipeline_result()
    bob_item = first.bundles[0].items[0].item.model_copy(update={"owner_email": "bob@example.com"})
    bob_bundle = first.bundles[0].model_copy(
        update={
            "owner_email": "bob@example.com",
            "items": [EnrichedActionItem(item=bob_item)],
        }
    )
    return first.model_copy(update={"bundles": [*first.bundles, bob_bundle]})


class FakeSource(MeetArtifactSource):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.subjects: list[str | None] = []

    def fetch(self, conference_id: str, subject: str | None = None) -> PipelineRequest:
        self.calls.append(conference_id)
        self.subjects.append(subject)
        return pipeline_request()


class FakeLedger:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.status: dict[str, str] = {}
        self.items: list[dict[str, Any]] = []
        self.onboarded: dict[str, OnboardedUser] = {
            "ana@example.com": OnboardedUser(
                user_id="101", email="ana@example.com", dm_space="spaces/ana"
            )
        }
        self.deliveries: dict[str, dict[str, str]] = {}
        self.mark_metadata: dict[str, dict[str, Any]] = {}
        self.onboarding_writes: list[OnboardedUser] = []
        self.offboarding_writes: list[dict[str, Any]] = []

    def claim_meeting(self, conference_id: str) -> bool:
        if conference_id in self.claimed:
            return False
        self.claimed.add(conference_id)
        return True

    def mark(
        self,
        conference_id: str,
        status: str,
        deliveries: dict[str, str] | None = None,
        **metadata: Any,
    ) -> None:
        self.status[conference_id] = status
        self.mark_metadata[conference_id] = metadata
        if deliveries is not None:
            self.deliveries[conference_id] = deliveries

    def onboarded_by_email(self) -> dict[str, OnboardedUser]:
        return self.onboarded

    def upsert_onboarded_user(
        self, *, user_id: str, email: str, dm_space: str | None
    ) -> OnboardedUser:
        user = OnboardedUser(user_id=user_id, email=email, dm_space=dm_space)
        self.onboarding_writes.append(user)
        self.onboarded[email.casefold()] = user
        return user

    def mark_offboarding(
        self, *, user_id: str, email: str | None = None, dm_space: str | None = None
    ) -> None:
        self.offboarding_writes.append({"user_id": user_id, "email": email, "dm_space": dm_space})

    def write_action_items(self, conference_id: str, bundles: Any, visible_to: Any) -> None:
        self.items.append({"conference_id": conference_id, "visible_to": visible_to})


class FakeScreen:
    def __init__(self, blocked: bool = False) -> None:
        self.blocked = blocked
        self.texts: list[str] = []

    def is_blocked(self, text: str) -> bool:
        self.texts.append(text)
        return self.blocked


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    def search(self, source: str, query: str, subject: str, limit: int) -> list[ContextMatch]:
        self.calls.append((source, query, subject, limit))
        if source not in {"google_docs", "google_tasks"}:
            raise ValueError("unsupported")
        return [
            ContextMatch(
                source_name=source,
                match_type=MatchType.RELATED_DOCUMENT,
                title="Launch plan",
                snippet="document last modified 2026-08-20",
            )
        ]


class RecordingDeliverer(Deliverer):
    def __init__(self, failures: set[str] | None = None) -> None:
        self.delivered: list[str] = []
        self.targets: list[OnboardedUser | None] = []
        self.meetings: list[MeetingHeader | None] = []
        self.failures = failures or set()

    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
        meeting: MeetingHeader | None = None,
    ) -> str:
        if owner_email in self.failures:
            raise RuntimeError("Chat unavailable")
        self.delivered.append(owner_email)
        self.targets.append(target)
        self.meetings.append(meeting)
        return f"fake:{owner_email}"


def push_body(conference_id: str = "abc123") -> dict[str, Any]:
    payload = json.dumps(
        {"transcript": {"name": f"conferenceRecords/{conference_id}/transcripts/t1"}}
    )
    return {"message": {"data": base64.b64encode(payload.encode()).decode(), "attributes": {}}}


def chat_push_body(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": encoded}}


def chat_event(event_type: str, *, include_email: bool = True) -> dict[str, Any]:
    user: dict[str, Any] = {"name": "users/303"}
    if include_email:
        user["email"] = "chat-user@example.com"
    return {
        "type": event_type,
        "user": user,
        "space": {
            "name": "spaces/chat-user",
            "spaceType": "DIRECT_MESSAGE",
            "singleUserBotDm": True,
        },
    }


def build(
    *,
    screen: FakeScreen | None = None,
    run_pipeline: Any = None,
    verifier: Any = None,
    ledger: FakeLedger | None = None,
    deliverer: RecordingDeliverer | None = None,
    trigger_sweep: Any = None,
    welcome_sender: Any = None,
    resolve_subject_email: Any = None,
    broker: Any = None,
    caller_token_verifier: Any = None,
    commitment_store: Any = None,
    reconcile_rows: Any = None,
    copilot_client: Any = None,
    chat_message_sender: Any = None,
) -> tuple[TestClient, FakeSource, FakeLedger, RecordingDeliverer, FakeScreen]:
    source = FakeSource()
    ledger = ledger or FakeLedger()
    deliverer = deliverer or RecordingDeliverer()
    screen = screen or FakeScreen()
    app = create_app(
        settings(),
        artifact_source=source,
        ledger=ledger,  # type: ignore[arg-type]
        deliverer=deliverer,
        screen=screen,  # type: ignore[arg-type]
        run_pipeline=run_pipeline or (lambda request: pipeline_result()),
        token_verifier=verifier or (lambda token: {"email": "push@test.iam.gserviceaccount.com"}),
        trigger_sweep=trigger_sweep or (lambda: None),
        welcome_sender=welcome_sender or (lambda user: None),
        resolve_subject_email=resolve_subject_email,
        broker=broker,
        caller_token_verifier=caller_token_verifier or (lambda token: {}),
        commitment_store=commitment_store,
        reconcile_rows=reconcile_rows,
        copilot_client=copilot_client,
        chat_message_sender=chat_message_sender,
    )
    client = TestClient(app, raise_server_exceptions=False)
    return client, source, ledger, deliverer, screen


AUTH = {"Authorization": "Bearer token"}


def test_context_broker_requires_its_own_bearer_verification() -> None:
    broker = FakeBroker()
    client, *_ = build(broker=broker)
    body = {
        "source": "google_docs",
        "query": "launch plan",
        "principal_email": "ana@example.com",
        "limit": 5,
    }

    assert client.post("/context/search", json=body).status_code == 403

    def reject(token: str) -> dict[str, Any]:
        raise PushAuthError("wrong caller")

    rejected, *_ = build(broker=broker, caller_token_verifier=reject)
    assert rejected.post("/context/search", json=body, headers=AUTH).status_code == 403
    assert broker.calls == []


def test_context_broker_refuses_non_onboarded_subject_without_searching() -> None:
    broker = FakeBroker()
    client, *_ = build(broker=broker)
    response = client.post(
        "/context/search",
        json={
            "source": "google_tasks",
            "query": "launch task",
            "principal_email": "outsider@example.com",
            "limit": 5,
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json() == {"matches": []}
    assert broker.calls == []


def test_context_broker_normalizes_subject_clamps_limit_and_serializes() -> None:
    broker = FakeBroker()
    client, *_ = build(broker=broker)
    response = client.post(
        "/context/search",
        json={
            "source": "google_docs",
            "query": "launch plan",
            "principal_email": " ANA@EXAMPLE.COM ",
            "limit": 999,
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    assert broker.calls == [("google_docs", "launch plan", "ana@example.com", 20)]
    assert response.json()["matches"][0]["match_type"] == "related_document"


def test_context_broker_rejects_unknown_source() -> None:
    broker = FakeBroker()
    client, *_ = build(broker=broker)
    response = client.post(
        "/context/search",
        json={
            "source": "buganizer",
            "query": "issue",
            "principal_email": "ana@example.com",
            "limit": 5,
        },
        headers=AUTH,
    )
    assert response.status_code == 400
    assert broker.calls == []


def test_context_broker_api_failure_is_contained() -> None:
    class FailingBroker(FakeBroker):
        def search(self, source: str, query: str, subject: str, limit: int) -> list[ContextMatch]:
            raise RuntimeError("query text must not be logged")

    client, *_ = build(broker=FailingBroker())
    response = client.post(
        "/context/search",
        json={
            "source": "google_docs",
            "query": "sensitive meeting text",
            "principal_email": "ana@example.com",
            "limit": 5,
        },
        headers=AUTH,
    )
    assert response.status_code == 502


def test_happy_path_delivers_writes_and_acks() -> None:
    client, source, ledger, deliverer, screen = build()
    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)
    assert response.status_code == 200
    assert source.calls == ["abc123"]
    assert deliverer.delivered == ["ana@example.com"]
    assert deliverer.meetings == [MeetingHeader(participant_names=("Bob",))]
    assert ledger.status["abc123"] == "delivered"
    assert ledger.deliveries["abc123"] == {"ana@example.com": "delivered"}
    assert ledger.items[0]["visible_to"] == ["ana@example.com", "bob@example.com"]
    assert screen.texts == ["hello"]


def test_not_onboarded_owner_is_skipped_but_items_are_written() -> None:
    ledger = FakeLedger()
    ledger.onboarded.clear()
    client, _, ledger, deliverer, _ = build(ledger=ledger)

    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)

    assert response.status_code == 200
    assert deliverer.delivered == []
    assert ledger.items
    assert ledger.status["abc123"] == "delivered"
    assert ledger.deliveries["abc123"] == {"ana@example.com": "skipped_not_onboarded"}


def test_one_delivery_failure_does_not_block_another_owner() -> None:
    ledger = FakeLedger()
    ledger.onboarded["bob@example.com"] = OnboardedUser(
        user_id="202", email="bob@example.com", dm_space="spaces/bob"
    )
    deliverer = RecordingDeliverer(failures={"ana@example.com"})
    client, _, ledger, deliverer, _ = build(
        ledger=ledger,
        deliverer=deliverer,
        run_pipeline=lambda request: two_owner_result(),
    )

    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)

    assert response.status_code == 200
    assert deliverer.delivered == ["bob@example.com"]
    assert ledger.status["abc123"] == "delivered_partial"
    assert ledger.deliveries["abc123"] == {
        "ana@example.com": "delivery_failed",
        "bob@example.com": "delivered",
    }
    assert ledger.items


def test_duplicate_event_is_acked_without_processing() -> None:
    client, source, _, deliverer, _ = build()
    assert client.post("/pubsub-push", json=push_body(), headers=AUTH).status_code == 200
    assert client.post("/pubsub-push", json=push_body(), headers=AUTH).status_code == 200
    assert source.calls == ["abc123"]
    assert deliverer.delivered == ["ana@example.com"]


def test_missing_or_invalid_token_is_403() -> None:
    def rejecting(token: str) -> dict[str, Any]:
        raise PushAuthError("bad")

    client, source, *_ = build(verifier=rejecting)
    assert client.post("/pubsub-push", json=push_body()).status_code == 403
    assert client.post("/pubsub-push", json=push_body(), headers=AUTH).status_code == 403
    assert source.calls == []


def test_blocked_transcript_is_observable_and_acked() -> None:
    client, _, ledger, deliverer, _ = build(screen=FakeScreen(blocked=True))
    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)
    assert response.status_code == 200
    assert ledger.status["abc123"] == "blocked"
    assert deliverer.delivered == []


def test_pipeline_failure_marks_failed_and_returns_500_for_retry() -> None:
    def failing(request: PipelineRequest) -> PipelineResult:
        raise RuntimeError("agent down")

    client, _, ledger, _, _ = build(run_pipeline=failing)
    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)
    assert response.status_code == 500
    assert ledger.status["abc123"] == "failed"


def test_event_without_conference_id_is_acked() -> None:
    client, source, *_ = build()
    body = {"message": {"data": base64.b64encode(b'{"noise": true}').decode()}}
    assert client.post("/pubsub-push", json=body, headers=AUTH).status_code == 200
    assert source.calls == []


def test_conference_id_extraction() -> None:
    assert extract_conference_id('{"name": "conferenceRecords/x_1-Y/transcripts/t"}') == "x_1-Y"
    assert extract_conference_id("no match") is None


def test_query_method_name_matches_deployment() -> None:
    from weave_ingestion.agent_client import QUERY_METHOD as caller

    from agent.deployment.deploy import QUERY_METHOD as deployed

    assert deployed == caller


def test_subscriber_id_is_read_from_cloudevent_source() -> None:
    attributes = {"ce-source": "//cloudidentity.googleapis.com/users/112655489411114378906"}
    assert extract_subscriber_user_id(attributes, "") == "112655489411114378906"


def test_subscriber_id_falls_back_to_the_payload() -> None:
    payload = '{"subscription":"//cloudidentity.googleapis.com/users/999"}'
    assert extract_subscriber_user_id({}, payload) == "999"


def test_subscriber_id_absent_returns_none() -> None:
    assert extract_subscriber_user_id({"ce-type": "x"}, "{}") is None


def live_settings() -> Settings:
    return settings().model_copy(
        update={"artifact_source": "live", "admin_subject": "admin@example.com"}
    )


def test_live_mode_impersonates_the_subscribing_user() -> None:
    source = FakeSource()
    ledger = FakeLedger()
    app = create_app(
        live_settings(),
        artifact_source=source,
        ledger=ledger,  # type: ignore[arg-type]
        deliverer=RecordingDeliverer(),
        screen=FakeScreen(),  # type: ignore[arg-type]
        run_pipeline=lambda request: pipeline_result(),
        token_verifier=lambda token: {},
        resolve_subject_email=lambda user_id: f"user-{user_id}@example.com",
    )
    client = TestClient(app, raise_server_exceptions=False)
    body = push_body()
    body["message"]["attributes"] = {"ce-source": "//cloudidentity.googleapis.com/users/424242"}
    assert client.post("/pubsub-push", json=body, headers=AUTH).status_code == 200
    # The fetch runs as the subscriber, not as a fixed configured account.
    assert source.subjects == ["user-424242@example.com"]


def test_live_mode_without_a_subscriber_id_fails_rather_than_guessing() -> None:
    source = FakeSource()
    ledger = FakeLedger()
    app = create_app(
        live_settings(),
        artifact_source=source,
        ledger=ledger,  # type: ignore[arg-type]
        deliverer=RecordingDeliverer(),
        screen=FakeScreen(),  # type: ignore[arg-type]
        run_pipeline=lambda request: pipeline_result(),
        token_verifier=lambda token: {},
        resolve_subject_email=lambda user_id: "should-not-be-called@example.com",
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)
    assert response.status_code == 500
    assert ledger.status["abc123"] == "failed"
    assert source.calls == []


def test_chat_added_event_onboards_triggers_and_sends_welcome() -> None:
    calls: list[str] = []
    welcomed: list[OnboardedUser] = []
    client, _, ledger, _, _ = build(
        trigger_sweep=lambda: calls.append("triggered"),
        welcome_sender=welcomed.append,
    )

    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("ADDED_TO_SPACE")),
        headers=AUTH,
    )

    assert response.status_code == 200
    assert calls == ["triggered"]
    assert ledger.onboarding_writes == [
        OnboardedUser(
            user_id="303",
            email="chat-user@example.com",
            dm_space="spaces/chat-user",
        )
    ]
    assert welcomed == ledger.onboarding_writes


def test_chat_added_event_resolves_missing_email() -> None:
    client, _, ledger, _, _ = build(
        resolve_subject_email=lambda user_id: f"resolved-{user_id}@example.com"
    )
    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("ADDED_TO_SPACE", include_email=False)),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert ledger.onboarding_writes[0].email == "resolved-303@example.com"


def test_chat_removed_event_writes_tombstone_and_triggers() -> None:
    calls: list[str] = []
    client, _, ledger, _, _ = build(trigger_sweep=lambda: calls.append("triggered"))
    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("REMOVED_FROM_SPACE")),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert calls == ["triggered"]
    assert ledger.offboarding_writes == [
        {
            "user_id": "303",
            "email": "chat-user@example.com",
            "dm_space": "spaces/chat-user",
        }
    ]


def test_unknown_and_malformed_chat_events_are_acked_without_writes() -> None:
    client, _, ledger, _, _ = build()
    unknown = client.post(
        "/chat-events",
        json=chat_push_body({"type": "MESSAGE"}),
        headers=AUTH,
    )
    malformed = client.post("/chat-events", json={"message": {"data": "not-base64"}}, headers=AUTH)
    assert unknown.status_code == 200
    assert malformed.status_code == 200
    assert ledger.onboarding_writes == []
    assert ledger.offboarding_writes == []


def test_chat_event_bad_oidc_is_rejected() -> None:
    def rejecting(token: str) -> dict[str, Any]:
        raise PushAuthError("bad")

    client, _, ledger, _, _ = build(verifier=rejecting)
    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("ADDED_TO_SPACE")),
        headers=AUTH,
    )
    assert response.status_code == 403


def test_chat_question_calls_copilot_as_resolved_principal_and_posts_reply() -> None:
    calls: list[tuple[str, str, str]] = []
    replies: list[tuple[str, str, str | None]] = []

    async def ask(principal: str, space: str, message: str) -> str:
        calls.append((principal, space, message))
        return "Your launch brief is overdue."

    payload = chat_event("MESSAGE")
    payload["message"] = {
        "name": "spaces/chat-user/messages/1",
        "text": "what needs attention?",
    }
    client, *_ = build(
        copilot_client=ask,
        chat_message_sender=lambda space, body, message_name: replies.append(
            (space, body["text"], message_name)
        ),
    )
    response = client.post("/chat-events", json=chat_push_body(payload), headers=AUTH)

    assert response.status_code == 200
    assert calls == [("chat-user@example.com", "spaces/chat-user", "what needs attention?")]
    assert replies == [
        (
            "spaces/chat-user",
            "Your launch brief is overdue.",
            "spaces/chat-user/messages/1",
        )
    ]


def test_chat_copilot_failure_acks_and_posts_fixed_apology() -> None:
    replies: list[str] = []

    async def fail(*args: Any) -> str:
        raise RuntimeError("engine unavailable")

    payload = chat_event("MESSAGE")
    payload["message"] = {"text": "what is stale?"}
    client, *_ = build(
        copilot_client=fail,
        chat_message_sender=lambda space, body, message_name: replies.append(body["text"]),
    )
    response = client.post("/chat-events", json=chat_push_body(payload), headers=AUTH)
    assert response.status_code == 200
    assert replies == ["Sorry, I couldn't check your commitments just now. Please try again."]


def test_slash_command_and_disabled_copilot_keep_onboarding_only() -> None:
    calls: list[str] = []
    payload = chat_event("MESSAGE")
    payload["message"] = {"text": "/help"}
    client, *_ = build(copilot_client=lambda *args: calls.append("called"))
    assert (
        client.post("/chat-events", json=chat_push_body(payload), headers=AUTH).status_code == 200
    )

    payload["message"] = {"text": "hello"}
    disabled, *_ = build()
    assert (
        disabled.post("/chat-events", json=chat_push_body(payload), headers=AUTH).status_code == 200
    )
    assert calls == []


class FakeCommitmentStore:
    def __init__(self, owner_match: bool = True) -> None:
        self.owner_match = owner_match
        self.close_calls: list[tuple[str, str, str]] = []

    def commitment_for_mention(self, mention_ref: str) -> str | None:
        assert mention_ref == "abc--chat-user@example.com--0"
        return "commitment-1"

    def close(self, commitment_id: str, owner: str, closed_by: str) -> bool:
        self.close_calls.append((commitment_id, owner, closed_by))
        return self.owner_match


def test_mark_done_maps_one_based_card_index_and_uses_clicker_identity() -> None:
    store = FakeCommitmentStore()
    confirmations: list[str] = []
    ledger = FakeLedger()
    ledger.onboarded["chat-user@example.com"] = OnboardedUser(
        user_id="303", email="chat-user@example.com", dm_space="spaces/chat-user"
    )
    client, *_ = build(
        ledger=ledger,
        commitment_store=store,
        resolve_subject_email=lambda user_id: "chat-user@example.com",
        chat_message_sender=lambda space, body, message_name: confirmations.append(body["text"]),
    )
    payload = chat_event("CARD_CLICKED")
    payload["action"] = {
        "actionMethodName": "mark_done",
        "parameters": [
            {"key": "conference_id", "value": "abc"},
            {"key": "item_index", "value": "1"},
        ],
    }
    response = client.post("/chat-events", json=chat_push_body(payload), headers=AUTH)
    assert response.status_code == 200
    assert store.close_calls == [("commitment-1", "chat-user@example.com", "card_click")]
    assert confirmations == ["Done — I marked that commitment complete in Weave."]


def test_reconciliation_failure_does_not_change_delivered_outcome() -> None:
    def fail(rows: Any) -> None:
        raise RuntimeError("reconcile unavailable")

    client, _, ledger, _, _ = build(reconcile_rows=fail)
    response = client.post("/pubsub-push", json=push_body(), headers=AUTH)
    assert response.status_code == 200
    assert ledger.status["abc123"] == "delivered"
    assert ledger.mark_metadata["abc123"] == {"reconcile": "failed"}
    assert ledger.onboarding_writes == []


def test_chat_job_trigger_failure_retries_without_welcome() -> None:
    welcomed: list[OnboardedUser] = []

    def fail() -> None:
        raise RuntimeError("Run API unavailable")

    client, _, ledger, _, _ = build(trigger_sweep=fail, welcome_sender=welcomed.append)
    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("ADDED_TO_SPACE")),
        headers=AUTH,
    )
    assert response.status_code == 500
    assert ledger.onboarding_writes
    assert welcomed == []


def test_welcome_failure_does_not_roll_back_onboarding() -> None:
    def fail(user: OnboardedUser) -> None:
        raise RuntimeError("Chat unavailable")

    client, _, ledger, _, _ = build(welcome_sender=fail)
    response = client.post(
        "/chat-events",
        json=chat_push_body(chat_event("ADDED_TO_SPACE")),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert ledger.onboarding_writes


def test_chat_card_click_is_acked_without_onboarding(
    caplog: Any,
) -> None:
    caplog.set_level(logging.INFO, logger="weave_ingestion.main")
    payload = chat_event("CARD_CLICKED")
    payload["action"] = {
        "actionMethodName": "accept_item",
        "parameters": [
            {"key": "conference_id", "value": "abc123"},
            {"key": "item_index", "value": "1"},
        ],
    }
    client, _, ledger, _, _ = build()

    response = client.post("/chat-events", json=chat_push_body(payload), headers=AUTH)

    assert response.status_code == 200
    assert ledger.onboarding_writes == []
    assert ledger.offboarding_writes == []
    record = next(
        record
        for record in caplog.records
        if record.message == "Chat action-card click acknowledged"
    )
    assert record.function == "accept_item"
    assert record.conference_id == "abc123"
    assert record.item_index == "1"
    assert record.user_id == "303"
