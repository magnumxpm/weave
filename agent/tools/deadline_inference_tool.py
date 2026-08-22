"""Conservative parsing for explicitly spoken relative deadlines."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_deadline(phrase: str, meeting_date: date) -> date | None:
    """Parse only supported, deterministic date phrases; never infer intent."""
    normalized = " ".join(phrase.strip().casefold().split())
    if not normalized:
        return None

    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return date.fromisoformat(normalized)
    except ValueError:
        return None

    if normalized == "tomorrow":
        return meeting_date + timedelta(days=1)
    if normalized in {"end of day", "end of the day"}:
        return meeting_date
    if normalized in {"end of week", "end of the week"}:
        return meeting_date + timedelta(days=(4 - meeting_date.weekday()) % 7)
    if normalized in {"end of month", "end of the month"}:
        last_day = calendar.monthrange(meeting_date.year, meeting_date.month)[1]
        return meeting_date.replace(day=last_day)
    if normalized == "next week":
        return meeting_date + timedelta(days=7 - meeting_date.weekday())

    if normalized in _WEEKDAYS:
        days_ahead = (_WEEKDAYS[normalized] - meeting_date.weekday()) % 7
        return meeting_date + timedelta(days=days_ahead or 7)

    relative = re.fullmatch(r"in (\d+) (day|days|week|weeks)", normalized)
    if relative:
        count = int(relative.group(1))
        multiplier = 7 if relative.group(2).startswith("week") else 1
        return meeting_date + timedelta(days=count * multiplier)
    return None


def infer_deadline(phrase: str, meeting_date: str) -> str | None:
    """Resolve a spoken deadline against the meeting's ISO date."""
    try:
        base_date = date.fromisoformat(meeting_date)
    except ValueError:
        return None
    resolved = parse_deadline(phrase, base_date)
    return resolved.isoformat() if resolved else None
