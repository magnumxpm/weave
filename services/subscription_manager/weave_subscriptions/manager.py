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

logger = logging.getLogger(__name__)

EVENT_TYPE = "google.workspace.meet.transcript.v2.fileGenerated"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
RENEW_WHEN_REMAINING_BELOW = 0.25  # fraction of the original TTL


@dataclass(frozen=True)
class SubscriptionOutcome:
    user: str
    action: str  # created | renewed | current | failed
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


def ensure_subscription(
    service: Any, user_id: str, topic: str, now: datetime | None = None
) -> SubscriptionOutcome:
    """Create the user's transcript subscription, or renew it when close to expiry.

    The target is always `users/me`: the service is built with credentials
    delegated to this specific user, so "me" is that user. Using the email
    directly fails (TARGET_RESOURCE_ACCESS_DENIED) because the API expects the
    numeric Cloud Identity id, which would need an extra Directory scope.
    """
    now = now or datetime.now(UTC)
    target = "//cloudidentity.googleapis.com/users/me"

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


def run(
    users: list[str],
    topic: str,
    build_service: Any,
    now: datetime | None = None,
) -> list[SubscriptionOutcome]:
    """Process every user; one user's failure never blocks the rest."""
    outcomes: list[SubscriptionOutcome] = []
    for user in users:
        try:
            outcomes.append(ensure_subscription(build_service(user), user, topic, now))
        except Exception as error:  # noqa: BLE001 - a broken user must not stop the sweep
            logger.exception("subscription sweep failed for user", extra={"user": user})
            outcomes.append(SubscriptionOutcome(user, "failed", str(error)))
    return outcomes
