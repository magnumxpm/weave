"""Deterministic, principal-scoped tools exposed to the copilot model."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from google.adk.tools.tool_context import ToolContext
from weave_common import decorate_rows

from agent.context_sources.broker_client import fetch_broker_matches
from agent.copilot.date_windows import parse_date_window
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
    return decorate_rows(rows, all_rows=all_rows, today=datetime.now(UTC).date())


def suggest_next_actions(limit: int, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Suggest which of my commitments to act on next, and what to do about each.

    Args:
        limit: How many to suggest. Use 3 unless the user asks for more.
    """
    principal = _principal(tool_context)
    if principal is None:
        return []
    rows = _store().list_commitments(principal)
    # Closed work is not a suggestion; everything else competes on rank.
    live = [row for row in rows if row.get("status") != "closed"]
    ranked = decorate_rows(live, all_rows=rows, today=datetime.now(UTC).date())
    return ranked[: max(1, min(limit or 3, 10))]


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
    if principal is None:
        return []
    stale = _store().stale(principal, days)
    if not stale:
        return []
    # The stale slice is ordered by last_mentioned, and unblock impact cannot be
    # computed from a slice, so rank against the owner's full set.
    return decorate_rows(
        stale, all_rows=_store().list_commitments(principal), today=datetime.now(UTC).date()
    )


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


def _date_window(when: str) -> tuple[date | None, date | None] | dict[str, Any]:
    timezone_name = os.environ.get("WORKSPACE_TIMEZONE", "")
    if not timezone_name:
        return {"error": "WORKSPACE_TIMEZONE is not configured"}
    try:
        window = parse_date_window(when, timezone_name)
        logger.info(
            "copilot date window resolved",
            extra={
                "has_start": window[0] is not None,
                "has_end": window[1] is not None,
                "timezone": timezone_name,
            },
        )
        return window
    except (KeyError, ValueError) as error:
        return {"error": str(error), "when": when}


def search_my_meetings(
    query: str, when: str, limit: int, tool_context: ToolContext
) -> list[dict[str, Any]]:
    """Search summaries of meetings I attended by topic and/or local date.

    Args:
        query: Topic text, or an empty string for date-only lookup.
        when: Empty/all, today, yesterday, weekday, last weekday, this week,
            last week, YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD.
        limit: Maximum summaries to return, from 1 through 20.
    """
    principal = _principal(tool_context)
    if principal is None:
        return []
    window = _date_window(when)
    if isinstance(window, dict):
        return [window]
    start, end = window
    return _store().search_meeting_summaries(
        principal, query, start, end, limit=max(1, min(limit or 10, 20))
    )


def get_meeting_summary(meeting_summary_ref: str, tool_context: ToolContext) -> dict[str, Any]:
    """Get one meeting summary only when that meeting is visible to me."""
    principal = _principal(tool_context)
    if principal is None:
        return {"found": False}
    row = _store().get_meeting_summary(principal, meeting_summary_ref)
    return {"found": row is not None, **(row or {})}


def list_my_commitment_mentions(when: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """List commitments assigned to me in meetings in a local-date window.

    Args:
        when: Today, yesterday, weekday, last weekday, this week, last week,
            YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD.
    """
    principal = _principal(tool_context)
    if principal is None:
        return []
    window = _date_window(when)
    if isinstance(window, dict):
        return [window]
    start, end = window
    if start is None and end is None:
        return [{"error": "when is required for commitment mention lookup"}]
    return _store().list_commitment_mentions(principal, start, end)


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
