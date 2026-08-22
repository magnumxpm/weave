from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from weave_subscriptions.manager import EVENT_TYPE, ensure_subscription, run

NOW = datetime(2026, 8, 22, tzinfo=UTC)
TOPIC = "projects/p/topics/meet-artifacts"


class FakeRequest:
    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self) -> Any:
        return self._response


class FakeSubscriptions:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.created: list[dict[str, Any]] = []
        self.reactivated: list[str] = []
        self.filters: list[str] = []

    def list(self, *, filter: str) -> FakeRequest:
        self.filters.append(filter)
        return FakeRequest({"subscriptions": self.existing})

    def create(self, *, body: dict[str, Any]) -> FakeRequest:
        self.created.append(body)
        return FakeRequest({"name": "subscriptions/new"})

    def reactivate(self, *, name: str, body: dict[str, Any]) -> FakeRequest:
        self.reactivated.append(name)
        return FakeRequest({"name": name})


class FakeService:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.subscription_service = FakeSubscriptions(existing or [])

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
    outcome = ensure_subscription(service, "user@example.com", TOPIC, NOW)

    assert outcome.action == "created"
    body = service.subscription_service.created[0]
    # "me" is the delegated user; an email here is rejected by the API.
    assert body["targetResource"] == "//cloudidentity.googleapis.com/users/me"
    assert body["eventTypes"] == [EVENT_TYPE]
    assert body["notificationEndpoint"]["pubsubTopic"] == TOPIC
    # Transcript content must never ride along in the event.
    assert body["payloadOptions"]["includeResource"] is False


def test_healthy_subscription_is_left_alone() -> None:
    service = FakeService([expiring_in(5)])
    outcome = ensure_subscription(service, "user@example.com", TOPIC, NOW)
    assert outcome.action == "current"
    assert service.subscription_service.created == []
    assert service.subscription_service.reactivated == []


def test_subscription_near_expiry_is_renewed() -> None:
    service = FakeService([expiring_in(1)])
    outcome = ensure_subscription(service, "user@example.com", TOPIC, NOW)
    assert outcome.action == "renewed"
    assert service.subscription_service.reactivated == ["subscriptions/existing"]


def test_expired_subscription_is_renewed() -> None:
    service = FakeService([expiring_in(-1)])
    assert ensure_subscription(service, "user@example.com", TOPIC, NOW).action == "renewed"


def test_deleted_subscription_is_replaced_not_reused() -> None:
    deleted = expiring_in(5) | {"state": "DELETED"}
    service = FakeService([deleted])
    assert ensure_subscription(service, "user@example.com", TOPIC, NOW).action == "created"


def test_one_user_failure_does_not_stop_the_sweep() -> None:
    services = {"good": FakeService(), "bad": None}

    def build(user: str) -> Any:
        if user == "bad":
            raise RuntimeError("delegation denied")
        return services["good"]

    outcomes = run(["bad", "good"], TOPIC, build, NOW)
    actions = {outcome.user: outcome.action for outcome in outcomes}
    assert actions == {"bad": "failed", "good": "created"}
