from datetime import date

import pytest

from agent.tools.deadline_inference_tool import infer_deadline, parse_deadline


@pytest.mark.parametrize(
    ("phrase", "meeting_date", "expected"),
    [
        ("2027-01-03", date(2026, 12, 31), date(2027, 1, 3)),
        ("tomorrow", date(2026, 12, 31), date(2027, 1, 1)),
        ("Friday", date(2026, 12, 31), date(2027, 1, 1)),
        ("Friday", date(2027, 1, 1), date(2027, 1, 8)),
        ("end of the day", date(2026, 8, 22), date(2026, 8, 22)),
        ("end of week", date(2026, 8, 22), date(2026, 8, 28)),
        ("end of month", date(2024, 2, 4), date(2024, 2, 29)),
        ("next week", date(2026, 8, 22), date(2026, 8, 24)),
        ("in 3 days", date(2026, 8, 22), date(2026, 8, 25)),
        ("in 2 weeks", date(2026, 8, 22), date(2026, 9, 5)),
        ("sometime soon", date(2026, 8, 22), None),
        ("2026-99-99", date(2026, 8, 22), None),
    ],
)
def test_parse_deadline(phrase: str, meeting_date: date, expected: date | None) -> None:
    assert parse_deadline(phrase, meeting_date) == expected


def test_tool_uses_iso_strings() -> None:
    assert infer_deadline("tomorrow", "2026-08-22") == "2026-08-23"
    assert infer_deadline("tomorrow", "not-a-date") is None
