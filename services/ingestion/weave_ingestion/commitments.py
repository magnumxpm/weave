"""Owner-scoped commitment graph derived from immutable action-item mentions."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from weave_common import (
    CommitmentMention,
    CommitmentState,
    MentionRelationship,
    ReconcileDecision,
    rank,
)

from weave_ingestion.firestore_client import ACTION_ITEMS, WrittenActionItem
from weave_ingestion.prompts.reconcile_prompt import RECONCILE_PROMPT

logger = logging.getLogger(__name__)
COMMITMENTS = "commitments"
MATCH_THRESHOLD = 0.80
BLOCKER_THRESHOLD = 0.80
CANDIDATE_WINDOW = 40


def commitment_id_for(mention_ref: str) -> str:
    """Return the replay-stable id for a commitment's first mention."""
    return str(uuid5(NAMESPACE_URL, f"weave-commitment:{mention_ref}"))


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _snapshot_data(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None or getattr(snapshot, "exists", True) is False:
        return None
    data = snapshot.to_dict()
    return data if isinstance(data, dict) and data else None


class CommitmentStore:
    """Firestore access with owner guards on every read and lifecycle write."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._client

    def candidates_for(
        self, owner_email: str, embedding: list[float] | None, limit: int = 8
    ) -> list[dict[str, Any]]:
        owner = owner_email.strip().casefold()
        if not owner or limit <= 0:
            return []
        if not embedding:
            return self._lexical_candidates(owner, "", limit)
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter
            from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
            from google.cloud.firestore_v1.vector import Vector

            snapshots = (
                self.client.collection(COMMITMENTS)
                .where(filter=FieldFilter("owner_email", "==", owner))
                .find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(embedding),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=max(limit * 2, limit),
                    distance_result_field="vector_distance",
                )
                .stream()
            )
            return [
                {
                    "commitment_id": snapshot.id,
                    **(snapshot.to_dict() or {}),
                    "similarity": max(
                        0.0,
                        min(
                            1.0,
                            1.0 - float((snapshot.to_dict() or {}).get("vector_distance", 1.0)),
                        ),
                    ),
                }
                for snapshot in snapshots
                if (snapshot.to_dict() or {}).get("status") != CommitmentState.CLOSED.value
            ][:limit]
        except Exception:  # noqa: BLE001 - lexical retrieval is the fail-safe
            logger.exception("commitment vector lookup failed; using lexical candidates")
            return self._lexical_candidates(owner, "", limit)

    def _lexical_candidates(self, owner: str, query: str, limit: int) -> list[dict[str, Any]]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        snapshots = list(
            self.client.collection(COMMITMENTS)
            .where(filter=FieldFilter("owner_email", "==", owner))
            .order_by("last_mentioned", direction="DESCENDING")
            .limit(CANDIDATE_WINDOW)
            .stream()
        )
        records = [
            {"commitment_id": snapshot.id, **(snapshot.to_dict() or {})}
            for snapshot in snapshots
            if (snapshot.to_dict() or {}).get("status") != CommitmentState.CLOSED.value
        ]
        if not query.strip():
            return records[:limit]
        titles = [str(record.get("title") or "") for record in records]
        return [records[index] for index, _ in rank(query, titles)[:limit]]

    def apply(
        self,
        decision: ReconcileDecision,
        mention: CommitmentMention,
        owner_email: str,
        mention_embedding: list[float] | None,
        *,
        deadline: date | None = None,
        blocked_by: str | None = None,
    ) -> str:
        owner = owner_email.strip().casefold()
        commitment_id = decision.matched_commitment_id or commitment_id_for(mention.mention_ref)
        reference = self.client.collection(COMMITMENTS).document(commitment_id)
        mention_reference = reference.collection("mentions").document(mention.mention_ref)
        action_reference = self.client.collection(ACTION_ITEMS).document(mention.mention_ref)
        now = datetime.now(UTC)

        def operation(transaction: Any | None = None) -> str:
            def getter(ref: Any) -> Any:
                return ref.get(transaction=transaction) if transaction else ref.get()

            mention_data = _snapshot_data(getter(mention_reference))
            if mention_data is not None:
                return commitment_id
            existing = _snapshot_data(getter(reference))
            inferred = decision.inferred_state
            if inferred is CommitmentState.CLOSED:
                inferred = CommitmentState.LIKELY_COMPLETE

            mention_payload = mention.model_dump(mode="json")
            if transaction:
                transaction.set(mention_reference, mention_payload)
            else:
                mention_reference.set(mention_payload)

            if existing is None:
                state = inferred
                blocked = [blocked_by] if blocked_by else []
                payload: dict[str, Any] = {
                    "owner_email": owner,
                    "title": decision.canonical_title,
                    "status": state.value,
                    "status_evidence": decision.state_evidence,
                    "status_confidence": decision.confidence
                    if state is CommitmentState.LIKELY_COMPLETE
                    else None,
                    "created_from": mention.mention_ref,
                    "first_seen": mention.meeting_date.isoformat(),
                    "last_mentioned": mention.meeting_date.isoformat(),
                    "mention_count": 1,
                    "deadline": deadline.isoformat() if deadline else None,
                    "waiting_on": decision.waiting_on,
                    "blocked_by": blocked,
                    "blocked_by_evidence": {blocked_by: mention.mention_ref} if blocked_by else {},
                    "closed_by": None,
                    "closed_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                if mention_embedding:
                    from google.cloud.firestore_v1.vector import Vector

                    payload["embedding"] = Vector(mention_embedding)
            else:
                existing_owner = str(existing.get("owner_email") or "").strip().casefold()
                if existing_owner != owner:
                    raise PermissionError("commitment owner mismatch")
                existing_deadline = _as_date(existing.get("deadline"))
                latest_deadline = max(
                    (value for value in (existing_deadline, deadline) if value is not None),
                    default=None,
                )
                closed = existing.get("status") == CommitmentState.CLOSED.value
                payload = {
                    "last_mentioned": max(
                        _as_date(existing.get("last_mentioned")) or mention.meeting_date,
                        mention.meeting_date,
                    ).isoformat(),
                    # Mentions do not arrive in meeting order. A backfill walks
                    # write order, so an older mention can merge into a
                    # commitment created from a newer one; without this the span
                    # last_mentioned - first_seen understates the carry-over age
                    # that is the whole point of tracking the commitment.
                    "first_seen": min(
                        _as_date(existing.get("first_seen")) or mention.meeting_date,
                        mention.meeting_date,
                    ).isoformat(),
                    "mention_count": int(existing.get("mention_count") or 0) + 1,
                    "deadline": latest_deadline.isoformat() if latest_deadline else None,
                    "updated_at": now,
                }
                if not closed:
                    payload.update(
                        title=decision.canonical_title,
                        status=inferred.value,
                        status_evidence=decision.state_evidence,
                        status_confidence=decision.confidence
                        if inferred is CommitmentState.LIKELY_COMPLETE
                        else None,
                        waiting_on=decision.waiting_on,
                    )
                    if blocked_by and blocked_by != commitment_id:
                        blocked = list(existing.get("blocked_by") or [])
                        if blocked_by not in blocked:
                            blocked.append(blocked_by)
                        evidence = dict(existing.get("blocked_by_evidence") or {})
                        evidence[blocked_by] = mention.mention_ref
                        payload.update(blocked_by=blocked, blocked_by_evidence=evidence)
                if mention_embedding and not closed:
                    from google.cloud.firestore_v1.vector import Vector

                    payload["embedding"] = Vector(mention_embedding)

            if transaction:
                transaction.set(reference, payload, merge=existing is not None)
                transaction.set(action_reference, {"commitment_id": commitment_id}, merge=True)
            else:
                reference.set(payload, merge=existing is not None)
                action_reference.set({"commitment_id": commitment_id}, merge=True)
            return commitment_id

        return self._transaction(operation)

    def _transaction(self, operation: Callable[[Any | None], str]) -> str:
        if not hasattr(self.client, "transaction"):
            return operation(None)
        from google.cloud import firestore

        transaction = self.client.transaction()
        return firestore.transactional(operation)(transaction)

    def close(self, commitment_id: str, owner_email: str, closed_by: str) -> bool:
        return self._set_lifecycle(commitment_id, owner_email, CommitmentState.CLOSED, closed_by)

    def reopen(self, commitment_id: str, owner_email: str) -> bool:
        return self._set_lifecycle(commitment_id, owner_email, CommitmentState.OPEN, None)

    def _set_lifecycle(
        self,
        commitment_id: str,
        owner_email: str,
        state: CommitmentState,
        closed_by: str | None,
    ) -> bool:
        reference = self.client.collection(COMMITMENTS).document(commitment_id)
        snapshot = reference.get()
        data = _snapshot_data(snapshot)
        owner = owner_email.strip().casefold()
        if data is None or str(data.get("owner_email") or "").strip().casefold() != owner:
            return False
        if data.get("status") == state.value:
            return True
        now = datetime.now(UTC)
        reference.set(
            {
                "status": state.value,
                "closed_by": closed_by,
                "closed_at": now if state is CommitmentState.CLOSED else None,
                "updated_at": now,
            },
            merge=True,
        )
        return True

    def commitment_for_mention(self, mention_ref: str) -> str | None:
        data = _snapshot_data(self.client.collection(ACTION_ITEMS).document(mention_ref).get())
        value = data.get("commitment_id") if data else None
        return value if isinstance(value, str) and value else None


def make_llm_decider() -> Callable[[str, list[dict[str, Any]]], ReconcileDecision]:
    """Build the small structured Vertex call lazily at ingestion startup."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ.get("PROJECT_ID"),
        location=os.environ.get("REGION", "us-central1"),
    )
    model = os.environ.get("WEAVE_MODEL", "gemini-2.5-flash")

    def decide(mention_text: str, candidates: list[dict[str, Any]]) -> ReconcileDecision:
        response = client.models.generate_content(
            model=model,
            contents=(
                f"{RECONCILE_PROMPT}\n\nNEW MENTION:\n{mention_text}\n\n"
                f"CANDIDATES:\n{json.dumps(candidates, default=str)}"
            ),
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=ReconcileDecision,
            ),
        )
        return ReconcileDecision.model_validate_json(response.text or "{}")

    return decide


def reconcile_meeting(
    store: CommitmentStore,
    llm_decide: Callable[[str, list[dict[str, Any]]], ReconcileDecision],
    rows: Iterable[WrittenActionItem],
) -> list[str]:
    """Fold mentions independently; ambiguity and model failures create new nodes."""
    from weave_ingestion.embeddings import embed_documents

    commitment_ids: list[str] = []
    failures: list[Exception] = []
    for row in rows:
        item = row.enriched.item
        excerpt = item.description
        candidates = store.candidates_for(row.owner_email, row.embedding)
        candidate_ids = {str(candidate.get("commitment_id")) for candidate in candidates}
        safe_candidates = [
            {
                key: candidate.get(key)
                for key in (
                    "commitment_id",
                    "title",
                    "status",
                    "status_evidence",
                    "first_seen",
                    "last_mentioned",
                    "mention_count",
                    "deadline",
                    "waiting_on",
                )
            }
            for candidate in candidates
        ]
        try:
            decision = llm_decide(excerpt, safe_candidates)
        except Exception:  # noqa: BLE001 - a mention must never be dropped
            logger.exception("commitment judgment failed; creating an original commitment")
            decision = ReconcileDecision(
                matched_commitment_id=None,
                confidence=0,
                relationship=MentionRelationship.ORIGINAL,
                canonical_title=(row.enriched.title or excerpt)[:160],
                inferred_state=CommitmentState.OPEN,
            )

        match_is_allowed = (
            decision.matched_commitment_id is not None
            and decision.confidence >= MATCH_THRESHOLD
            and decision.matched_commitment_id in candidate_ids
        )
        if not match_is_allowed:
            decision = decision.model_copy(
                update={
                    "matched_commitment_id": None,
                    "relationship": MentionRelationship.ORIGINAL,
                    "inferred_state": CommitmentState.OPEN
                    if decision.inferred_state is CommitmentState.CLOSED
                    else decision.inferred_state,
                }
            )
        elif decision.inferred_state is CommitmentState.CLOSED:
            decision = decision.model_copy(
                update={"inferred_state": CommitmentState.LIKELY_COMPLETE}
            )
        if not decision.canonical_title.strip():
            decision = decision.model_copy(
                update={"canonical_title": (row.enriched.title or excerpt)[:160]}
            )

        blocked_by = None
        if decision.blocking_hint:
            try:
                blocker_vectors = embed_documents([decision.blocking_hint])
                blocker_candidates = store.candidates_for(
                    row.owner_email, blocker_vectors[0] if blocker_vectors else None
                )
                blocker_candidates = [
                    candidate
                    for candidate in blocker_candidates
                    if candidate.get("commitment_id") != decision.matched_commitment_id
                ]
                if (
                    blocker_candidates
                    and float(blocker_candidates[0].get("similarity") or 0) >= BLOCKER_THRESHOLD
                ):
                    blocked_by = str(blocker_candidates[0].get("commitment_id"))
            except Exception:  # noqa: BLE001 - an uncertain edge is dropped, never guessed
                logger.exception("blocking hint resolution failed; dropping dependency edge")

        mention = CommitmentMention(
            mention_ref=row.mention_ref,
            meeting_date=row.meeting_date,
            relationship=decision.relationship,
            excerpt=excerpt,
        )
        try:
            commitment_ids.append(
                store.apply(
                    decision,
                    mention,
                    row.owner_email,
                    row.embedding,
                    deadline=item.deadline,
                    blocked_by=blocked_by,
                )
            )
        except Exception as error:  # noqa: BLE001 - try every mention before marking failed
            logger.exception(
                "commitment persistence failed", extra={"mention_ref": row.mention_ref}
            )
            failures.append(error)
    if failures:
        raise RuntimeError(f"failed to persist {len(failures)} commitment mentions") from failures[
            0
        ]
    return commitment_ids
