"""Firestore ledger: idempotency lease and owner-visible action item writes."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from weave_common import (
    EnrichedActionItem,
    EnrichedOwnerBundle,
    MeetingSummaryContent,
    PipelineRequest,
    PipelineResult,
)

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
MEETINGS = "processed_meetings"
ACTION_ITEMS = "action_items"
MEETING_SUMMARIES = "meeting_summaries"
ONBOARDED = "onboarded_users"
MAX_BATCH_WRITES = 500


def meeting_summary_ref(conference_record_id: str) -> str:
    meeting_id = conference_record_id.rsplit("/", 1)[-1]
    return f"{MEETING_SUMMARIES}/{meeting_id}"


@dataclass(frozen=True)
class WrittenActionItem:
    """One persisted mention and the vector already computed for it."""

    mention_ref: str
    owner_email: str
    meeting_date: date
    enriched: EnrichedActionItem
    embedding: list[float] | None
    meeting_summary_ref: str | None = None


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
        **metadata: Any,
    ) -> None:
        update: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if deliveries is not None:
            # Firestore field paths interpret dots as nesting. Workspace email
            # addresses contain dots, so make them safe as delivery-map keys.
            update["deliveries"] = {
                email.strip().casefold().replace(".", ","): outcome
                for email, outcome in deliveries.items()
            }
        update.update(metadata)
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
    ) -> list[WrittenActionItem]:
        """Persist items with the meeting's attendee list as the ACL."""
        documents, written = self._action_item_documents(conference_id, bundles, visible_to)
        collection = self.client.collection(ACTION_ITEMS)
        for document_id, document in documents:
            collection.document(document_id).set(document)
        return written

    def _action_item_documents(
        self,
        conference_id: str,
        bundles: list[EnrichedOwnerBundle],
        visible_to: list[str],
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[WrittenActionItem]]:
        from google.cloud.firestore_v1.vector import Vector

        from weave_ingestion.embeddings import DIMENSIONS, embed_documents

        now = datetime.now(UTC)
        summary_ref = meeting_summary_ref(conference_id)
        normalized_visible_to = sorted(
            {email.strip().casefold() for email in visible_to if email.strip()}
        )
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

        written: list[WrittenActionItem] = []
        documents: list[tuple[str, dict[str, Any]]] = []
        for row_index, (bundle, index, enriched) in enumerate(rows):
            item = enriched.item
            document = {
                "conference_record_id": conference_id,
                "meeting_summary_ref": summary_ref,
                "description": item.description,
                "source_text": item.source_text,
                "references": [reference.model_dump(mode="json") for reference in item.references],
                "owner_email": bundle.owner_email,
                "status": item.status.value,
                "deadline": item.deadline.isoformat() if item.deadline else None,
                "blocked_on": item.blocked_on,
                "title": enriched.title,
                "details": enriched.details,
                "meeting_date": bundle.meeting_date.isoformat(),
                "visible_to": normalized_visible_to,
                "created_at": now,
            }
            if vectors is not None:
                document["embedding"] = Vector(vectors[row_index])
            mention_ref = f"{conference_id}--{bundle.owner_email}--{index}"
            documents.append((mention_ref, document))
            written.append(
                WrittenActionItem(
                    mention_ref=mention_ref,
                    owner_email=bundle.owner_email.strip().casefold(),
                    meeting_date=bundle.meeting_date,
                    enriched=enriched,
                    embedding=vectors[row_index] if vectors is not None else None,
                    meeting_summary_ref=summary_ref,
                )
            )
        return documents, written

    def _summary_document(
        self,
        request: PipelineRequest,
        summary: MeetingSummaryContent,
        visible_to: list[str],
    ) -> dict[str, Any]:
        from google.cloud.firestore_v1.vector import Vector

        from weave_ingestion.embeddings import DIMENSIONS, embed_documents

        content = summary.model_dump(mode="json")
        text = "\n".join(
            [
                request.meeting_title or "",
                summary.overview,
                *summary.topics,
                *summary.decisions,
                *summary.implementation_notes,
                *summary.reproduction_steps,
            ]
        )
        vector: list[float] | None = None
        try:
            vectors = (self._embed_documents_fn or embed_documents)([text])
            if len(vectors) != 1 or len(vectors[0]) != DIMENSIONS:
                raise ValueError("embedding response shape does not match meeting summary")
            vector = vectors[0]
        except Exception:  # noqa: BLE001 - summary remains searchable lexically
            logger.exception("meeting-summary embedding failed; writing lexical summary")

        now = datetime.now(UTC)
        document: dict[str, Any] = {
            "conference_record_id": request.conference_record_id,
            "meeting_summary_ref": meeting_summary_ref(request.conference_record_id),
            "meeting_title": request.meeting_title,
            "meeting_date": request.meeting_date.isoformat(),
            "started_at": request.started_at,
            **content,
            "visible_to": sorted(
                {email.strip().casefold() for email in visible_to if email.strip()}
            ),
            "created_at": now,
            "updated_at": now,
        }
        if vector is not None:
            document["embedding"] = Vector(vector)
        return document

    def persist_meeting(
        self,
        conference_id: str,
        request: PipelineRequest,
        result: PipelineResult,
        visible_to: list[str],
    ) -> list[WrittenActionItem]:
        """Atomically publish one meeting's summary and immutable action mentions."""
        if result.conference_record_id != request.conference_record_id:
            raise ValueError("pipeline result conference does not match request")
        documents, written = self._action_item_documents(conference_id, result.bundles, visible_to)
        if len(documents) + 1 > MAX_BATCH_WRITES:
            raise ValueError("meeting exceeds Firestore atomic batch limit")

        summary_id = request.conference_record_id.rsplit("/", 1)[-1]
        summary_document = self._summary_document(request, result.summary, visible_to)
        if hasattr(self.client, "batch"):
            batch = self.client.batch()
            for document_id, document in documents:
                batch.set(self.client.collection(ACTION_ITEMS).document(document_id), document)
            batch.set(
                self.client.collection(MEETING_SUMMARIES).document(summary_id),
                summary_document,
            )
            batch.commit()
        else:  # Lightweight local fakes; production Firestore always supports batches.
            for document_id, document in documents:
                self.client.collection(ACTION_ITEMS).document(document_id).set(document)
            self.client.collection(MEETING_SUMMARIES).document(summary_id).set(summary_document)
        logger.info(
            "meeting summary and action items persisted",
            extra={
                "conference_id": conference_id,
                "action_item_count": len(written),
                "attendee_count": len(summary_document["visible_to"]),
                "summary_embedded": "embedding" in summary_document,
            },
        )
        return written
