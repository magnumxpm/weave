"""Idempotently derive commitments from historical action-item mentions."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Any

from weave_common import (
    ActionItem,
    ActionType,
    CommitmentStatus,
    EnrichedActionItem,
)
from weave_ingestion.commitments import CommitmentStore, make_llm_decider, reconcile_meeting
from weave_ingestion.firestore_client import WrittenActionItem


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _vector(value: Any) -> list[float] | None:
    """Read a stored embedding back as plain floats.

    `Vector` is a Sequence, so iterating it is the whole job; an earlier version
    reached into `to_map_value()` for a "values" key that does not exist (the
    key is "value") and only worked by falling through to this. Losing the
    vector here is invisible -- candidate retrieval just silently degrades to
    the recency fallback -- so keep the one path that is exercised.
    """
    try:
        values = list(value)
    except TypeError:
        return None
    return [float(number) for number in values] if values else None


def main() -> None:
    from google.cloud import firestore

    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    args = parser.parse_args()
    client = firestore.Client(project=args.project)
    store = CommitmentStore(client)
    decide = make_llm_decider()
    processed = 0
    query = client.collection("action_items").order_by("created_at")
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        owner = str(data.get("owner_email") or "").strip().casefold()
        if not owner or not data.get("meeting_date"):
            continue
        status_raw = str(data.get("status") or CommitmentStatus.ACCEPTED.value)
        try:
            status = CommitmentStatus(status_raw)
        except ValueError:
            status = CommitmentStatus.ACCEPTED
        item = ActionItem(
            description=str(data.get("description") or data.get("title") or "Action item"),
            source_text=data.get("source_text"),
            action_type=ActionType.TASK,
            status=status,
            owner_email=owner,
            owner_confidence=1.0,
            resolution_turn_ref=0 if status is CommitmentStatus.ACCEPTED else None,
            deadline=_date(data["deadline"]) if data.get("deadline") else None,
        )
        row = WrittenActionItem(
            mention_ref=snapshot.id,
            owner_email=owner,
            meeting_date=_date(data["meeting_date"]),
            enriched=EnrichedActionItem(
                item=item,
                title=data.get("title"),
                details=data.get("details"),
            ),
            embedding=_vector(data.get("embedding")),
            meeting_summary_ref=data.get("meeting_summary_ref"),
        )
        reconcile_meeting(store, decide, [row])
        processed += 1
    print(f"reconciled_mentions={processed}")


if __name__ == "__main__":
    main()
