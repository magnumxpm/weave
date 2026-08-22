"""Firestore ledger: idempotency lease and owner-visible action item writes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from weave_common import EnrichedOwnerBundle

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
MEETINGS = "processed_meetings"
ACTION_ITEMS = "action_items"


class MeetingLedger:
    """Wraps the two collections the pipeline writes; client is injectable."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client()
        return self._client

    def claim_meeting(self, conference_id: str) -> bool:
        """Atomically claim a conference for processing.

        Create is the atomic primitive: it fails if the doc exists. A doc in
        `failed`, or in `processing` with an expired lease, may be re-claimed
        (Pub/Sub redelivery after a crash). Terminal states are never re-claimed.
        """
        from google.api_core import exceptions

        now = datetime.now(UTC)
        reference = self.client.collection(MEETINGS).document(conference_id)
        claim = {"status": "processing", "lease_expires_at": now + LEASE, "claimed_at": now}
        try:
            reference.create(claim)
            return True
        except exceptions.AlreadyExists:
            snapshot = reference.get()
            data = snapshot.to_dict() or {}
            status = data.get("status")
            lease_expired = (expiry := data.get("lease_expires_at")) is None or expiry <= now
            if status == "failed" or (status == "processing" and lease_expired):
                reference.set(claim)
                return True
            logger.info("duplicate event ignored", extra={"conference_id": conference_id})
            return False

    def mark(self, conference_id: str, status: str) -> None:
        self.client.collection(MEETINGS).document(conference_id).set(
            {"status": status, "updated_at": datetime.now(UTC)}, merge=True
        )

    def write_action_items(
        self,
        conference_id: str,
        bundles: list[EnrichedOwnerBundle],
        visible_to: list[str],
    ) -> None:
        """Persist items with the meeting's attendee list as the ACL."""
        now = datetime.now(UTC)
        collection = self.client.collection(ACTION_ITEMS)
        for bundle in bundles:
            for index, enriched in enumerate(bundle.items):
                item = enriched.item
                collection.document(f"{conference_id}--{bundle.owner_email}--{index}").set(
                    {
                        "conference_record_id": conference_id,
                        "description": item.description,
                        "owner_email": bundle.owner_email,
                        "status": item.status.value,
                        "deadline": item.deadline.isoformat() if item.deadline else None,
                        "visible_to": visible_to,
                        "created_at": now,
                    }
                )
