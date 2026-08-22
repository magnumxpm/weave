from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from weave_ingestion.firestore_client import OnboardedUser
from weave_subscriptions.manager import EVENT_TYPE, delete_subscriptions, ensure_subscription, run

NOW = datetime(2026, 8, 22, tzinfo=UTC)
TOPIC = "projects/p/topics/meet-artifacts"
USER_ID = "112655489411114378906"


class FakeRequest:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    def execute(self) -> Any:
        if self._error:
            raise self._error
        return self._response


class FakeSubscriptions:
    def __init__(self, existing: list[dict[str, Any]], *, delete_fails: bool = False) -> None:
        self.existing = existing
        self.created: list[dict[str, Any]] = []
        self.reactivated: list[str] = []
        self.filters: list[str] = []
        self.deleted: list[str] = []
        self.delete_fails = delete_fails

    def list(self, *, filter: str) -> FakeRequest:
        self.filters.append(filter)
        return FakeRequest({"subscriptions": self.existing})

    def create(self, *, body: dict[str, Any]) -> FakeRequest:
        self.created.append(body)
        return FakeRequest({"name": "subscriptions/new"})

    def reactivate(self, *, name: str, body: dict[str, Any]) -> FakeRequest:
        self.reactivated.append(name)
        return FakeRequest({"name": name})

    def delete(self, *, name: str) -> FakeRequest:
        self.deleted.append(name)
        error = RuntimeError("delete failed") if self.delete_fails else None
        return FakeRequest({}, error)


class FakeService:
    def __init__(
        self,
        existing: list[dict[str, Any]] | None = None,
        *,
        delete_fails: bool = False,
    ) -> None:
        self.subscription_service = FakeSubscriptions(existing or [], delete_fails=delete_fails)

    def subscriptions(self) -> FakeSubscriptions:
        return self.subscription_service


def expiring_in(days: float) -> dict[str, Any]:
    expiry = NOW + timedelta(days=days)
    return {
        "name": "subscriptions/existing",
        "state": "ACTIVE",
        "expireTime": expiry.isoformat().replace("+00:00", "Z"),
    }


def test_creates_subscription_targeting_the_user_and_topic() -> None:
    service = FakeService()
    outcome = ensure_subscription(service, USER_ID, TOPIC, NOW)

    assert outcome.action == "created"
    body = service.subscription_service.created[0]
    assert body["targetResource"] == f"//cloudidentity.googleapis.com/users/{USER_ID}"
    assert body["eventTypes"] == [EVENT_TYPE]
    assert body["notificationEndpoint"]["pubsubTopic"] == TOPIC
    # Transcript content must never ride along in the event.
    assert body["payloadOptions"]["includeResource"] is False


def test_healthy_subscription_is_left_alone() -> None:
    service = FakeService([expiring_in(5)])
    outcome = ensure_subscription(service, USER_ID, TOPIC, NOW)
    assert outcome.action == "current"
    assert service.subscription_service.created == []
    assert service.subscription_service.reactivated == []


def test_subscription_near_expiry_is_renewed() -> None:
    service = FakeService([expiring_in(1)])
    outcome = ensure_subscription(service, USER_ID, TOPIC, NOW)
    assert outcome.action == "renewed"
    assert service.subscription_service.reactivated == ["subscriptions/existing"]


def test_expired_subscription_is_renewed() -> None:
    service = FakeService([expiring_in(-1)])
    assert ensure_subscription(service, USER_ID, TOPIC, NOW).action == "renewed"


def test_deleted_subscription_is_replaced_not_reused() -> None:
    deleted = expiring_in(5) | {"state": "DELETED"}
    service = FakeService([deleted])
    assert ensure_subscription(service, USER_ID, TOPIC, NOW).action == "created"


def active_user(user_id: str = USER_ID, email: str = "user@example.com") -> OnboardedUser:
    return OnboardedUser(user_id=user_id, email=email, status="active")


def offboarding_user() -> OnboardedUser:
    return OnboardedUser(
        user_id=USER_ID,
        email="user@example.com",
        dm_space="spaces/dm",
        status="offboarding",
    )


def test_one_user_failure_does_not_stop_the_sweep() -> None:
    broken, working = "999", USER_ID

    def build(email: str) -> Any:
        if email == "broken@example.com":
            raise RuntimeError("delegation denied")
        return FakeService()

    outcomes = run(
        [active_user(broken, "broken@example.com"), active_user(working)],
        TOPIC,
        build,
        NOW,
    )
    actions = {outcome.user: outcome.action for outcome in outcomes}
    assert actions == {broken: "failed", working: "created"}


def test_service_impersonation_uses_email_while_target_uses_numeric_id() -> None:
    service = FakeService()
    subjects: list[str] = []

    def build(email: str) -> FakeService:
        subjects.append(email)
        return service

    outcomes = run([active_user(email="someone@example.com")], TOPIC, build, NOW)
    assert outcomes[0].action == "created"
    assert subjects == ["someone@example.com"]
    target = service.subscription_service.created[0]["targetResource"]
    assert target == f"//cloudidentity.googleapis.com/users/{USER_ID}"


def test_delete_subscriptions_removes_every_live_match() -> None:
    service = FakeService(
        [
            expiring_in(5),
            expiring_in(4) | {"name": "subscriptions/second"},
            expiring_in(3) | {"name": "subscriptions/gone", "state": "DELETED"},
        ]
    )
    outcome = delete_subscriptions(service, USER_ID)
    assert outcome.action == "deleted"
    assert service.subscription_service.deleted == [
        "subscriptions/existing",
        "subscriptions/second",
    ]


def test_offboarding_deletes_subscription_then_document() -> None:
    service = FakeService([expiring_in(5)])
    deleted_documents: list[str] = []
    outcomes = run(
        [offboarding_user()],
        TOPIC,
        lambda email: service,
        NOW,
        delete_onboarded=deleted_documents.append,
    )
    assert outcomes[0].action == "deleted"
    assert deleted_documents == [USER_ID]


def test_offboarding_failure_keeps_tombstone() -> None:
    service = FakeService([expiring_in(5)], delete_fails=True)
    deleted_documents: list[str] = []
    outcomes = run(
        [offboarding_user()],
        TOPIC,
        lambda email: service,
        NOW,
        delete_onboarded=deleted_documents.append,
    )
    assert outcomes[0].action == "failed"
    assert deleted_documents == []
