"""Deterministic, principal-scoped tools exposed to the copilot model."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from google.adk.tools.tool_context import ToolContext

from agent.context_sources.broker_client import fetch_broker_matches
from agent.copilot.store_reader import CopilotStoreReader

logger = logging.getLogger(__name__)
ALLOWED_EVIDENCE_SOURCES = frozenset({"google_docs", "google_tasks"})
COMMITMENT_STATES = frozenset({"open", "waiting", "likely_complete", "closed"})
# Spellings a model reaches for when it means "do not filter". "" is the
# documented value, but "all" is what it actually sends.
EVERY_STATUS = frozenset({"", "all", "any", "*"})


@lru_cache(maxsize=1)
def _store() -> CopilotStoreReader:
    return CopilotStoreReader()


def _principal(tool_context: ToolContext) -> str | None:
    value = tool_context.state.get("copilot_principal")
    if not isinstance(value, str) or "@" not in value:
        logger.warning("copilot tool refused: no valid principal")
        return None
    return value.strip().casefold()


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


def _transitive_dependents(commitments: list[dict[str, Any]]) -> dict[str, int]:
    open_ids = {
        str(row["commitment_id"])
        for row in commitments
        if row.get("status") != "closed" and row.get("commitment_id")
    }
    reverse: dict[str, set[str]] = {item: set() for item in open_ids}
    for row in commitments:
        dependent = str(row.get("commitment_id") or "")
        if dependent not in open_ids:
            continue
        for blocker in row.get("blocked_by") or []:
            if blocker in open_ids:
                reverse.setdefault(str(blocker), set()).add(dependent)

    counts: dict[str, int] = {}
    for root in open_ids:
        seen: set[str] = set()
        frontier = list(reverse.get(root, set()))
        while frontier:
            current = frontier.pop()
            if current in seen or current == root:
                continue
            seen.add(current)
            frontier.extend(reverse.get(current, set()))
        counts[root] = len(seen)
    return counts


def _attention_score(row: dict[str, Any], dependent_count: int, today: date) -> int:
    deadline = _as_date(row.get("deadline"))
    last = _as_date(row.get("last_mentioned")) or today
    score = dependent_count * 200
    if deadline and deadline < today and row.get("status") != "closed":
        score += 1000 + min((today - deadline).days, 365)
    if row.get("status") == "waiting" and (today - last).days >= 7:
        score += 500 + min((today - last).days, 365)
    if row.get("status") == "open":
        score += min(int(row.get("mention_count") or 0), 50) * 10
    if row.get("status") == "likely_complete":
        score -= 50
    return score


def list_my_commitments(status_filter: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """List my commitments, most urgent first.

    Args:
        status_filter: One of "open", "waiting", "likely_complete", "closed",
            or "" / "all" for every status. Any other value is an error.
    """
    principal = _principal(tool_context)
    if principal is None:
        return []
    normalized_filter = status_filter.strip().casefold()
    if normalized_filter in EVERY_STATUS:
        normalized_filter = ""
    elif normalized_filter not in COMMITMENT_STATES:
        # Never answer an unusable filter with []: the model cannot tell that
        # apart from "you have no commitments" and will report absence as fact.
        logger.warning("copilot rejected unknown status filter")
        return [
            {
                "error": f"unknown status_filter {status_filter!r}",
                "valid_status_filter_values": sorted(COMMITMENT_STATES | {"all"}),
            }
        ]
    rows = _store().list_commitments(principal, normalized_filter)
    all_rows = rows if not normalized_filter else _store().list_commitments(principal)
    dependents = _transitive_dependents(all_rows)
    today = datetime.now(UTC).date()
    for row in rows:
        item_id = str(row.get("commitment_id") or "")
        row["attention_score"] = _attention_score(row, dependents.get(item_id, 0), today)
        row["open_dependents"] = dependents.get(item_id, 0)
    return sorted(
        rows,
        key=lambda row: (
            -int(row["attention_score"]),
            str(row.get("deadline") or "9999-12-31"),
            str(row.get("commitment_id") or ""),
        ),
    )


def get_commitment_history(commitment_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Get my commitment and its chronological, immutable mention timeline."""
    principal = _principal(tool_context)
    if principal is None:
        return {"found": False}
    history = _store().history(principal, commitment_id)
    return {"found": history is not None, **(history or {})}


def find_stale_commitments(days: int, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Find my open or waiting commitments not mentioned for the requested age."""
    principal = _principal(tool_context)
    return [] if principal is None else _store().stale(principal, days)


def trace_blockers(commitment_id: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Walk my unresolved blocker chain, capped at five levels and cycle-safe."""
    principal = _principal(tool_context)
    if principal is None:
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item_id: str, depth: int) -> None:
        if depth > 5 or item_id in seen:
            return
        seen.add(item_id)
        current = _store().get_commitment(principal, item_id)
        if current is None:
            return
        evidence = current.get("blocked_by_evidence") or {}
        for blocker_id in current.get("blocked_by") or []:
            blocker = _store().get_commitment(principal, str(blocker_id))
            if blocker is None or blocker.get("status") == "closed":
                continue
            result.append(
                {
                    "blocked_commitment_id": item_id,
                    "blocker": blocker,
                    "evidence_mention_ref": evidence.get(blocker_id),
                    "evidence_excerpt": _store().mention_excerpt(
                        item_id, str(evidence.get(blocker_id) or "")
                    ),
                    "depth": depth,
                }
            )
            visit(str(blocker_id), depth + 1)

    visit(commitment_id, 1)
    return result


def search_my_history(query: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Search raw meeting mentions that are visible to me."""
    principal = _principal(tool_context)
    return [] if principal is None else _store().search_history(principal, query)


def search_workspace_evidence(
    source: str, query: str, tool_context: ToolContext
) -> list[dict[str, Any]]:
    """Search my Drive document metadata or unfinished Google Tasks through the broker."""
    principal = _principal(tool_context)
    normalized_source = source.strip().casefold()
    if principal is None or normalized_source not in ALLOWED_EVIDENCE_SOURCES:
        return []
    base_url = os.environ.get("CONTEXT_BROKER_URL", "")
    audience = os.environ.get("CONTEXT_BROKER_AUDIENCE", "")
    if not base_url or not audience:
        return []
    try:
        return [
            match.model_dump(mode="json")
            for match in fetch_broker_matches(
                base_url, audience, normalized_source, query[:400], principal, 10
            )
        ]
    except Exception:  # noqa: BLE001 - evidence outages are empty evidence, never agent failure
        logger.exception("copilot workspace evidence search failed")
        return []


def close_commitment(commitment_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Close one of my Weave commitments after my explicit confirmation."""
    principal = _principal(tool_context)
    closed = principal is not None and _store().close(principal, commitment_id)
    return {"updated": closed, "commitment_id": commitment_id if closed else None}


def reopen_commitment(commitment_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Reopen one of my explicitly closed Weave commitments."""
    principal = _principal(tool_context)
    reopened = principal is not None and _store().reopen(principal, commitment_id)
    return {"updated": reopened, "commitment_id": commitment_id if reopened else None}
