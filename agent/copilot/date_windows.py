"""Deterministic local-calendar windows for Copilot retrieval tools."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_date_window(
    expression: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> tuple[date | None, date | None]:
    """Resolve a supported expression to an inclusive local-date window."""
    value = expression.strip().casefold()
    if value in {"", "all", "any"}:
        return None, None

    local_today = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone_name)).date()
    if value == "today":
        return local_today, local_today
    if value == "yesterday":
        day = local_today - timedelta(days=1)
        return day, day
    if value == "this week":
        start = local_today - timedelta(days=local_today.weekday())
        return start, local_today
    if value == "last week":
        end = local_today - timedelta(days=local_today.weekday() + 1)
        return end - timedelta(days=6), end

    if value.startswith("last ") and value.removeprefix("last ") in WEEKDAYS:
        weekday = WEEKDAYS[value.removeprefix("last ")]
        delta = (local_today.weekday() - weekday) % 7 or 7
        day = local_today - timedelta(days=delta)
        return day, day
    if value in WEEKDAYS:
        day = local_today - timedelta(days=(local_today.weekday() - WEEKDAYS[value]) % 7)
        return day, day

    if ".." in value:
        start_raw, end_raw = value.split("..", 1)
        try:
            start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        except ValueError as error:
            raise ValueError("date range must use YYYY-MM-DD..YYYY-MM-DD") from error
        if end < start:
            raise ValueError("date range end must not precede start")
        return start, end

    try:
        day = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            "when must be today, yesterday, a weekday, last <weekday>, this week, "
            "last week, YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD"
        ) from error
    return day, day
