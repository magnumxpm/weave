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


def run(
    users: list[str],
    topic: str,
    build_service: Any,
    now: datetime | None = None,
    resolve_user_id: Any = None,
) -> list[SubscriptionOutcome]:
    """Process every user; one user's failure never blocks the rest.

    Entries may be numeric Cloud Identity ids or email addresses. Emails need
    `resolve_user_id`, which requires the Directory scope on this service
    account's delegation; numeric ids work with the Meet scope alone.
    """
    outcomes: list[SubscriptionOutcome] = []
    for user in users:
        try:
            user_id = user if is_numeric_id(user) else None
            if user_id is None:
                if resolve_user_id is None:
                    raise ValueError(
                        f"{user} is an email but no directory resolver is configured; "
                        "grant admin.directory.user.readonly to this service account's "
                        "delegation or list the numeric id instead"
                    )
                user_id = resolve_user_id(user)
            outcomes.append(ensure_subscription(build_service(user), user_id, topic, now))
        except Exception as error:  # noqa: BLE001 - a broken user must not stop the sweep
            logger.exception("subscription sweep failed for user", extra={"user": user})
            outcomes.append(SubscriptionOutcome(user, "failed", str(error)))
    return outcomes
