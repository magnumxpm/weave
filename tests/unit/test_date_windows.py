from datetime import UTC, date, datetime

import pytest

from agent.copilot.date_windows import parse_date_window

NOW = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)  # Monday, Aug 31 in Asia/Kolkata.


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("today", (date(2026, 8, 31), date(2026, 8, 31))),
        ("yesterday", (date(2026, 8, 30), date(2026, 8, 30))),
        ("Monday", (date(2026, 8, 31), date(2026, 8, 31))),
        ("last monday", (date(2026, 8, 24), date(2026, 8, 24))),
        ("this week", (date(2026, 8, 31), date(2026, 8, 31))),
        ("last week", (date(2026, 8, 24), date(2026, 8, 30))),
        ("2026-08-01", (date(2026, 8, 1), date(2026, 8, 1))),
        ("2026-08-01..2026-08-03", (date(2026, 8, 1), date(2026, 8, 3))),
        ("all", (None, None)),
    ],
)
def test_date_windows_use_the_configured_local_calendar(
    expression: str, expected: tuple[date | None, date | None]
) -> None:
    assert parse_date_window(expression, "Asia/Kolkata", now=NOW) == expected


@pytest.mark.parametrize("expression", ["someday", "2026-09-02..2026-09-01"])
def test_invalid_date_windows_are_errors(expression: str) -> None:
    with pytest.raises(ValueError):
        parse_date_window(expression, "Asia/Kolkata", now=NOW)
