#!/usr/bin/env python3
"""Backfill action-item vectors in re-runnable batches.

Documents without an ``embedding`` field are invisible to vector search. Run
this as part of the rollout after the vector index is READY; lexical fallback
continues to serve searches while the backfill is in progress.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Iterable, Sequence
from itertools import islice
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from weave_ingestion.embeddings import DIMENSIONS, embed_documents

BATCH_SIZE = 20
logger = logging.getLogger(__name__)


def _batches(values: Iterable[Any], size: int) -> Iterable[list[Any]]:
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def backfill(
    client: Any,
    embed_documents_fn: Callable[[Sequence[str]], list[list[float]]] = embed_documents,
) -> int:
    """Embed every action item that does not already contain a vector."""
    missing = (
        snapshot
        for snapshot in client.collection("action_items").stream()
        if "embedding" not in (snapshot.to_dict() or {})
    )
    updated = 0
    for snapshots in _batches(missing, BATCH_SIZE):
        texts = []
        records = []
        for snapshot in snapshots:
            item = snapshot.to_dict() or {}
            records.append(item)
            title = item.get("title") or item.get("description") or "Prior action item"
            texts.append(f"{title}\n{item.get('details') or ''}")
        vectors = embed_documents_fn(texts)
        if len(vectors) != len(snapshots) or any(len(vector) != DIMENSIONS for vector in vectors):
            raise ValueError("embedding response shape does not match backfill batch")
        for snapshot, item, vector in zip(snapshots, records, vectors, strict=True):
            update: dict[str, Any] = {"embedding": Vector(vector)}
            # Items written before meeting_date existed would otherwise reach the
            # agent with no date at all, leaving it unable to judge staleness.
            # The write timestamp is same-day in practice and is the only date
            # these documents carry.
            if not item.get("meeting_date") and (created_at := item.get("created_at")):
                update["meeting_date"] = created_at.date().isoformat()
            snapshot.reference.update(update)
            updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="GCP project id; ADC default when omitted")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    count = backfill(firestore.Client(project=args.project))
    logger.info("embedding backfill complete", extra={"updated_count": count})
    print(f"Updated {count} action items.")


if __name__ == "__main__":
    main()
