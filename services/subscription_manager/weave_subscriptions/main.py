"""Cloud Run job entrypoint for the per-user subscription sweep."""

from __future__ import annotations

import json
import logging
import os
import sys

from weave_subscriptions.manager import MEET_SCOPE, run

logger = logging.getLogger(__name__)


def _build_service(user_email: str):
    from googleapiclient.discovery import build
    from weave_ingestion.google_auth import delegated_credentials

    service_account = os.environ["SUBSCRIPTIONS_SA"]
    credentials = delegated_credentials(user_email, [MEET_SCOPE], service_account)
    return build("workspaceevents", "v1", credentials=credentials)


def main() -> int:
    from weave_ingestion.firestore_client import MeetingLedger
    from weave_ingestion.logging_config import configure_logging

    configure_logging()

    ledger = MeetingLedger()
    users = ledger.onboarded_users()
    topic = os.environ["MEET_TOPIC"]
    if not users:
        logger.warning("no onboarded users configured")
        return 0

    outcomes = run(users, topic, _build_service, delete_onboarded=ledger.delete_onboarded_user)
    for outcome in outcomes:
        logger.info(
            "subscription outcome",
            extra={"user": outcome.user, "action": outcome.action, "detail": outcome.detail},
        )
    failures = [o for o in outcomes if o.action == "failed"]
    logger.info(
        "subscription sweep complete",
        extra={"total": len(outcomes), "failed": len(failures)},
    )
    # Non-zero exit surfaces in the job's status so the alert on subscription
    # count vs group size is not the only signal.
    print(json.dumps([o.__dict__ for o in outcomes]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
