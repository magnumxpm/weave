"""Firestore ledger: idempotency lease and owner-visible action item writes."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from weave_common import EnrichedOwnerBundle

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
MEETINGS = "processed_meetings"
ACTION_ITEMS = "action_items"
ONBOARDED = "onboarded_users"


class OnboardedUser(BaseModel):
    """Storage-backed delivery identity, internal to the ingestion boundary."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    user_id: str
    email: str
    dm_space: str | None = None
    status: Literal["active", "offboarding"] = "active"

    @field_validator("user_id")
    @classmethod
    def numeric_user_id(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("user_id must be a numeric Cloud Identity id")
        return value

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized:
            raise ValueError("email must be a Workspace email address")
        return normalized

    @field_validator("dm_space")
    @classmethod
    def valid_dm_space(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("spaces/"):
            raise ValueError("dm_space must use the spaces/{id} resource format")
        return value


class MeetingLedger:
    """Wrap pipeline and onboarding collections; the client is injectable."""

    def __init__(
        self,
        client: Any | None = None,
        embed_documents_fn: Callable[[Sequence[str]], list[list[float]]] | None = None,
    ) -> None:
        self._client = client
        self._embed_documents_fn = embed_documents_fn

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

    def mark(
        self,
        conference_id: str,
        status: str,
        deliveries: dict[str, str] | None = None,
    ) -> None:
        update: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if deliveries is not None:
            # Firestore field paths interpret dots as nesting. Workspace email
            # addresses contain dots, so make them safe as delivery-map keys.
            update["deliveries"] = {
                email.strip().casefold().replace(".", ","): outcome
                for email, outcome in deliveries.items()
            }
        self.client.collection(MEETINGS).document(conference_id).set(update, merge=True)

    def onboarded_users(self) -> list[OnboardedUser]:
        """Return all well-formed onboarding records in one collection scan."""
        users: list[OnboardedUser] = []
        for snapshot in self.client.collection(ONBOARDED).stream():
            data = snapshot.to_dict() or {}
            data.setdefault("user_id", snapshot.id)
            try:
                users.append(OnboardedUser.model_validate(data))
            except ValueError:
                logger.exception("invalid onboarding record", extra={"user_id": snapshot.id})
        return users

    def onboarded_by_email(self) -> dict[str, OnboardedUser]:
        """Return active users keyed by normalized email for delivery lookup."""
        return {
            user.email.strip().casefold(): user
            for user in self.onboarded_users()
            if user.status == "active"
        }

    def upsert_onboarded_user(
        self,
        *,
        user_id: str,
        email: str,
        dm_space: str | None,
    ) -> OnboardedUser:
        """Activate a user from a Chat install signal.

        Installing the app is an opt-in signal only. Domain-wide delegation,
        not this document, remains the authority for Workspace data access.
        """
        # Validate before writing. A record that fails validation is skipped by
        # onboarded_users() forever, so it must never reach storage: the user
        # would appear onboarded while silently receiving nothing.
        user = OnboardedUser(user_id=user_id, email=email, dm_space=dm_space, status="active")
        now = datetime.now(UTC)
        reference = self.client.collection(ONBOARDED).document(user.user_id)
        existing = reference.get().to_dict() or {}
        reference.set(
            {
                "user_id": user.user_id,
                "email": user.email,
                "dm_space": user.dm_space,
                "status": "active",
                "onboarded_at": existing.get("onboarded_at", now),
                "updated_at": now,
            },
            merge=True,
        )
        return user

    def mark_offboarding(
        self,
        *,
        user_id: str,
        email: str | None = None,
        dm_space: str | None = None,
    ) -> None:
        """Leave a tombstone so the delegated job can delete subscriptions."""
        update: dict[str, Any] = {
            "user_id": user_id,
            "status": "offboarding",
            "updated_at": datetime.now(UTC),
        }
        if email:
            update["email"] = email.strip().casefold()
        if dm_space:
            update["dm_space"] = dm_space
        self.client.collection(ONBOARDED).document(user_id).set(update, merge=True)

    def delete_onboarded_user(self, user_id: str) -> None:
        self.client.collection(ONBOARDED).document(user_id).delete()

    def write_action_items(
        self,
        conference_id: str,
        bundles: list[EnrichedOwnerBundle],
        visible_to: list[str],
    ) -> None:
        """Persist items with the meeting's attendee list as the ACL."""
        from google.cloud.firestore_v1.vector import Vector

        from weave_ingestion.embeddings import DIMENSIONS, embed_documents

        now = datetime.now(UTC)
        collection = self.client.collection(ACTION_ITEMS)
        rows = [
            (bundle, index, enriched)
            for bundle in bundles
            for index, enriched in enumerate(bundle.items)
        ]
        texts = [
            f"{enriched.title or enriched.item.description}\n{enriched.details or ''}"
            for _, _, enriched in rows
        ]
        vectors: list[list[float]] | None = None
        if texts:
            try:
                vectors = (self._embed_documents_fn or embed_documents)(texts)
                if len(vectors) != len(texts) or any(
                    len(vector) != DIMENSIONS for vector in vectors
                ):
                    raise ValueError("embedding response shape does not match action items")
            except Exception:  # noqa: BLE001 - history writes survive embedding outages
                logger.exception("action-item embedding failed; writing lexical history only")
                vectors = None

        for row_index, (bundle, index, enriched) in enumerate(rows):
            item = enriched.item
            document = {
                "conference_record_id": conference_id,
                "description": item.description,
                "source_text": item.source_text,
                "references": [reference.model_dump(mode="json") for reference in item.references],
                "owner_email": bundle.owner_email,
                "status": item.status.value,
                "deadline": item.deadline.isoformat() if item.deadline else None,
                "title": enriched.title,
                "details": enriched.details,
                "meeting_date": bundle.meeting_date.isoformat(),
                "visible_to": visible_to,
                "created_at": now,
            }
            if vectors is not None:
                document["embedding"] = Vector(vectors[row_index])
            collection.document(f"{conference_id}--{bundle.owner_email}--{index}").set(document)
