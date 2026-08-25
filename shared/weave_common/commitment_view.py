"""Presentation-neutral view of a commitment, shared by every surface.

Google Chat gets cards and Gemini Enterprise gets prose, but both must state the
same facts and the same reasons. So the judgement lives here, in pure functions
over raw commitment rows, and each surface only chooses how to draw the result.

The copilot tools return rows already decorated by `decorate_rows`, which is what
keeps the two surfaces honest: the model reports a reason string it was handed
rather than composing its own, and the card renderer reads the identical field.

Every optional field is None (or empty) when the underlying fact is absent. That
is deliberate and load-bearing -- "show it only when relevant" is a property of
the data here, not a rule each renderer has to remember.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from weave_common.schemas import FrozenModel

DUE_SOON_DAYS = 3
STALE_DAYS = 14
WAITING_ATTENTION_DAYS = 7


class UrgencyGroup(StrEnum):
    """Why an item needs attention. Declaration order is display order."""

    OVERDUE = "overdue"
    DUE_SOON = "due_soon"
    BLOCKING = "blocking"
    WAITING = "waiting"
    STALE = "stale"
    ACTIVE = "active"
    LIKELY_COMPLETE = "likely_complete"
    CLOSED = "closed"


GROUP_LABELS: dict[UrgencyGroup, str] = {
    UrgencyGroup.OVERDUE: "Overdue",
    UrgencyGroup.DUE_SOON: "Due soon",
    UrgencyGroup.BLOCKING: "Holding up other work",
    UrgencyGroup.WAITING: "Waiting on someone",
    UrgencyGroup.STALE: "Going quiet",
    UrgencyGroup.ACTIVE: "In progress",
    UrgencyGroup.LIKELY_COMPLETE: "Probably done",
    UrgencyGroup.CLOSED: "Closed",
}

GROUP_ICONS: dict[UrgencyGroup, str] = {
    UrgencyGroup.OVERDUE: "error",
    UrgencyGroup.DUE_SOON: "schedule",
    UrgencyGroup.BLOCKING: "account_tree",
    UrgencyGroup.WAITING: "hourglass_empty",
    UrgencyGroup.STALE: "notifications_paused",
    UrgencyGroup.ACTIVE: "radio_button_unchecked",
    UrgencyGroup.LIKELY_COMPLETE: "task_alt",
    UrgencyGroup.CLOSED: "check_circle",
}

_GROUP_ORDER = {group: index for index, group in enumerate(UrgencyGroup)}


def as_date(value: Any) -> date | None:
    """Coerce Firestore's date-ish values; anything unparseable is no date."""
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


def transitive_dependents(commitments: list[dict[str, Any]]) -> dict[str, int]:
    """Count open commitments each row transitively unblocks."""
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


def attention_score(row: dict[str, Any], dependent_count: int, today: date) -> int:
    """Rank within a group; the group itself does the coarse ordering.

    A missed deadline is a promise already broken, so it outweighs unblock
    impact: overdue adds 1000 while each unblocked commitment adds 200, meaning
    roughly five dependents to match one overdue day. That ratio is a product
    judgement, not arithmetic -- change it here and both surfaces follow.
    """
    deadline = as_date(row.get("deadline"))
    last = as_date(row.get("last_mentioned")) or today
    score = dependent_count * 200
    if deadline and deadline < today and row.get("status") != "closed":
        score += 1000 + min((today - deadline).days, 365)
    if row.get("status") == "waiting" and (today - last).days >= WAITING_ATTENTION_DAYS:
        score += 500 + min((today - last).days, 365)
    if row.get("status") == "open":
        score += min(int(row.get("mention_count") or 0), 50) * 10
    if row.get("status") == "likely_complete":
        score -= 50
    return score


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _span(days: int) -> str:
    if days >= 14:
        return _plural(days // 7, "week")
    return _plural(days, "day")


def _urgency(row: dict[str, Any], dependents: int, today: date) -> UrgencyGroup:
    status = str(row.get("status") or "open")
    if status == "closed":
        return UrgencyGroup.CLOSED
    if status == "likely_complete":
        return UrgencyGroup.LIKELY_COMPLETE
    deadline = as_date(row.get("deadline"))
    if deadline and deadline < today:
        return UrgencyGroup.OVERDUE
    if deadline and (deadline - today).days <= DUE_SOON_DAYS:
        return UrgencyGroup.DUE_SOON
    if dependents:
        return UrgencyGroup.BLOCKING
    if status == "waiting":
        return UrgencyGroup.WAITING
    last = as_date(row.get("last_mentioned"))
    if last and (today - last).days >= STALE_DAYS:
        return UrgencyGroup.STALE
    return UrgencyGroup.ACTIVE


def _reason(row: dict[str, Any], group: UrgencyGroup, dependents: int, today: date) -> str:
    """One phrase saying why this sits where it sits.

    Only facts actually present may appear: never "overdue" without a deadline.
    """
    deadline = as_date(row.get("deadline"))
    last = as_date(row.get("last_mentioned"))
    if group is UrgencyGroup.OVERDUE and deadline:
        return f"Overdue by {_span((today - deadline).days)}"
    if group is UrgencyGroup.DUE_SOON and deadline:
        days = (deadline - today).days
        return "Due today" if days == 0 else f"Due in {_span(days)}"
    if group is UrgencyGroup.BLOCKING:
        return f"Holding up {_plural(dependents, 'other commitment')}"
    if group is UrgencyGroup.WAITING:
        waiting_on = str(row.get("waiting_on") or "").strip()
        if waiting_on and last:
            return f"Waiting on {waiting_on} for {_span((today - last).days)}"
        if waiting_on:
            return f"Waiting on {waiting_on}"
        return "Waiting on someone else"
    if group is UrgencyGroup.STALE and last:
        return f"Not mentioned for {_span((today - last).days)}"
    if group is UrgencyGroup.LIKELY_COMPLETE:
        confidence = row.get("status_confidence")
        if isinstance(confidence, (int, float)):
            return f"Looks done ({round(float(confidence) * 100)}% confident)"
        return "Looks done, never confirmed"
    if group is UrgencyGroup.CLOSED:
        return "Closed"
    count = int(row.get("mention_count") or 1)
    if count > 1:
        return f"Raised in {_plural(count, 'meeting')}"
    return "Open, no deadline set"


def _carry_over(row: dict[str, Any]) -> str | None:
    """The span the commitment graph exists to make visible."""
    count = int(row.get("mention_count") or 1)
    if count < 2:
        return None
    first, last = as_date(row.get("first_seen")), as_date(row.get("last_mentioned"))
    if first and last and last > first:
        return f"Raised in {_plural(count, 'meeting')} over {_span((last - first).days)}"
    return f"Raised in {_plural(count, 'meeting')}"


class CommitmentView(FrozenModel):
    """What a surface needs to draw one commitment, and nothing more."""

    commitment_id: str
    title: str
    status: str
    urgency: UrgencyGroup
    urgency_label: str
    icon: str
    reason: str
    attention_score: int = 0
    deadline: date | None = None
    waiting_on: str | None = None
    blocked_by_titles: tuple[str, ...] = ()
    open_dependents: int = 0
    mention_count: int = 1
    carry_over: str | None = None
    status_evidence: str | None = None
    status_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def present_facts(self) -> frozenset[str]:
        """Which optional facts this view actually carries, for renderers and tests."""
        facts = {
            "deadline": self.deadline is not None,
            "waiting_on": bool(self.waiting_on),
            "blocked_by": bool(self.blocked_by_titles),
            "open_dependents": self.open_dependents > 0,
            "carry_over": self.carry_over is not None,
            "status_evidence": bool(self.status_evidence),
        }
        return frozenset(name for name, present in facts.items() if present)


def decorate_rows(
    rows: list[dict[str, Any]],
    *,
    all_rows: list[dict[str, Any]] | None = None,
    today: date,
) -> list[dict[str, Any]]:
    """Add ranking and reason fields to raw rows, most urgent first.

    `all_rows` is the owner's full set, needed because unblock impact cannot be
    computed from a filtered slice. It defaults to `rows` for the unfiltered case.
    """
    universe = all_rows if all_rows is not None else rows
    dependents = transitive_dependents(universe)
    decorated: list[dict[str, Any]] = []
    for row in rows:
        item_id = str(row.get("commitment_id") or "")
        count = dependents.get(item_id, 0)
        group = _urgency(row, count, today)
        decorated.append(
            {
                **row,
                "attention_score": attention_score(row, count, today),
                "open_dependents": count,
                "urgency": group.value,
                "urgency_label": GROUP_LABELS[group],
                "attention_reason": _reason(row, group, count, today),
                "carry_over_summary": _carry_over(row),
            }
        )
    return sorted(
        decorated,
        key=lambda row: (
            -int(row["attention_score"]),
            str(row.get("deadline") or "9999-12-31"),
            str(row.get("commitment_id") or ""),
        ),
    )


def build_views(
    rows: list[dict[str, Any]],
    *,
    all_rows: list[dict[str, Any]] | None = None,
    today: date,
) -> list[CommitmentView]:
    """Turn raw or already-decorated rows into ordered views."""
    universe = all_rows if all_rows is not None else rows
    decorated = (
        rows
        if rows and all("attention_reason" in row for row in rows)
        else decorate_rows(rows, all_rows=universe, today=today)
    )
    titles = {str(row.get("commitment_id") or ""): str(row.get("title") or "") for row in universe}
    views: list[CommitmentView] = []
    for row in decorated:
        group = UrgencyGroup(str(row.get("urgency") or UrgencyGroup.ACTIVE.value))
        # Unresolved blocker ids are dropped rather than shown as raw uuids: a
        # blocker we cannot name is one the reader cannot act on.
        blockers = tuple(
            titles[str(blocker)]
            for blocker in row.get("blocked_by") or []
            if titles.get(str(blocker))
        )
        likely_complete = row.get("status") == "likely_complete"
        confidence = row.get("status_confidence")
        views.append(
            CommitmentView(
                commitment_id=str(row.get("commitment_id") or ""),
                title=str(row.get("title") or "Untitled commitment"),
                status=str(row.get("status") or "open"),
                urgency=group,
                urgency_label=str(row.get("urgency_label") or GROUP_LABELS[group]),
                icon=GROUP_ICONS[group],
                reason=str(row.get("attention_reason") or ""),
                attention_score=int(row.get("attention_score") or 0),
                deadline=as_date(row.get("deadline")),
                waiting_on=str(row["waiting_on"]) if row.get("waiting_on") else None,
                blocked_by_titles=blockers,
                open_dependents=int(row.get("open_dependents") or 0),
                mention_count=max(1, int(row.get("mention_count") or 1)),
                carry_over=row.get("carry_over_summary"),
                status_evidence=(
                    str(row["status_evidence"])
                    if likely_complete and row.get("status_evidence")
                    else None
                ),
                status_confidence=(
                    float(confidence)
                    if likely_complete and isinstance(confidence, (int, float))
                    else None
                ),
            )
        )
    return views


class UrgencyBucket(FrozenModel):
    group: UrgencyGroup
    label: str
    views: tuple[CommitmentView, ...]


def group_views(views: list[CommitmentView]) -> list[UrgencyBucket]:
    """Bucket views by urgency in display order, dropping empty groups."""
    buckets: dict[UrgencyGroup, list[CommitmentView]] = {}
    for view in views:
        buckets.setdefault(view.urgency, []).append(view)
    return [
        UrgencyBucket(group=group, label=GROUP_LABELS[group], views=tuple(members))
        for group, members in sorted(buckets.items(), key=lambda pair: _GROUP_ORDER[pair[0]])
        if members
    ]


def summarize(views: list[CommitmentView]) -> str:
    """A one-line count for a card subtitle; only non-zero facts appear."""
    if not views:
        return "Nothing open"
    open_count = sum(1 for view in views if view.status not in {"closed", "likely_complete"})
    parts = [_plural(open_count, "open commitment")] if open_count else []
    for group, noun in (
        (UrgencyGroup.OVERDUE, "overdue"),
        (UrgencyGroup.LIKELY_COMPLETE, "probably done"),
    ):
        count = sum(1 for view in views if view.urgency is group)
        if count:
            parts.append(f"{count} {noun}")
    return " · ".join(parts) if parts else _plural(len(views), "commitment")
