"""Ensure every onboarded user has a live Meet transcript subscription.

There is no org-wide Meet subscription: one subscription per user is the real
scaling unit, so this job impersonates each user via domain-wide delegation and
creates or renews their subscription. Runs on a schedule; renewal happens well
before expiry so a missed run never silently drops a user's meetings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from weave_ingestion.firestore_client import OnboardedUser

logger = logging.getLogger(__name__)

EVENT_TYPE = "google.workspace.meet.transcript.v2.fileGenerated"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
RENEW_WHEN_REMAINING_BELOW = 0.25  # fraction of the original TTL


@dataclass(frozen=True)
class SubscriptionOutcome:
    user: str
    action: str  # created | renewed | current | deleted | absent | failed
    detail: str = ""


def _needs_renewal(subscription: dict[str, Any], now: datetime) -> bool:
    expire_time = subscription.get("expireTime")
    if not expire_time:
        return False  # a subscription without an expiry never needs renewal
    expiry = datetime.fromisoformat(expire_time.replace("Z", "+00:00"))
    remaining = expiry - now
    if remaining <= timedelta(0):
        return True
    # Workspace subscriptions are capped at 7 days; renew inside the last quarter.
    return remaining < timedelta(days=7) * RENEW_WHEN_REMAINING_BELOW


def is_numeric_id(user: str) -> bool:
    return user.isdigit()


def ensure_subscription(
    service: Any, user_id: str, topic: str, now: datetime | None = None
) -> SubscriptionOutcome:
    """Create the user's transcript subscription, or renew it when close to expiry.

    `user_id` must already be the numeric Cloud Identity id. Neither an email
    address nor the literal "me" is accepted here: both return
    TARGET_RESOURCE_ACCESS_DENIED from the Meet backend.
    """
    now = now or datetime.now(UTC)
    if not is_numeric_id(user_id):
        raise ValueError(f"expected a numeric Cloud Identity id, got {user_id!r}")
    target = f"//cloudidentity.googleapis.com/users/{user_id}"

    # The delegated caller can only ever see its own subscriptions, so filtering
    # on event type alone is already user-scoped.
    existing = (
        service.subscriptions()
        .list(filter=f'event_types:"{EVENT_TYPE}"')
        .execute()
        .get("subscriptions", [])
    )
    live = [s for s in existing if s.get("state") != "DELETED"]

    if not live:
        service.subscriptions().create(
            body={
                "targetResource": target,
                "eventTypes": [EVENT_TYPE],
                "notificationEndpoint": {"pubsubTopic": topic},
                "payloadOptions": {"includeResource": False},
            }
        ).execute()
        return SubscriptionOutcome(user_id, "created")

    subscription = live[0]
    if _needs_renewal(subscription, now):
        service.subscriptions().reactivate(name=subscription["name"], body={}).execute()
        return SubscriptionOutcome(user_id, "renewed", subscription["name"])
    return SubscriptionOutcome(user_id, "current", subscription.get("expireTime", ""))


def delete_subscriptions(service: Any, user_id: str) -> SubscriptionOutcome:
    """Delete every live transcript subscription visible to the delegated user."""
    if not is_numeric_id(user_id):
        raise ValueError(f"expected a numeric Cloud Identity id, got {user_id!r}")
    existing = (
        service.subscriptions()
        .list(filter=f'event_types:"{EVENT_TYPE}"')
        .execute()
        .get("subscriptions", [])
    )
    live = [subscription for subscription in existing if subscription.get("state") != "DELETED"]
    for subscription in live:
        service.subscriptions().delete(name=subscription["name"]).execute()
    names = ",".join(subscription["name"] for subscription in live)
    return SubscriptionOutcome(user_id, "deleted" if live else "absent", names)


def run(
    users: list[OnboardedUser],
    topic: str,
    build_service: Any,
    now: datetime | None = None,
    delete_onboarded: Any = None,
) -> list[SubscriptionOutcome]:
    """Reconcile every Firestore onboarding record independently."""
    outcomes: list[SubscriptionOutcome] = []
    for user in users:
        try:
            if not user.email:
                raise ValueError(f"onboarding record {user.user_id} has no email")
            service = build_service(user.email)
            if user.status == "active":
                outcome = ensure_subscription(service, user.user_id, topic, now)
            else:
                outcome = delete_subscriptions(service, user.user_id)
                if delete_onboarded is None:
                    raise ValueError("delete_onboarded callback is required for offboarding")
                delete_onboarded(user.user_id)
            outcomes.append(outcome)
        except Exception as error:  # noqa: BLE001 - a broken user must not stop the sweep
            logger.exception("subscription sweep failed for user", extra={"user_id": user.user_id})
            outcomes.append(SubscriptionOutcome(user.user_id, "failed", str(error)))
    return outcomes
