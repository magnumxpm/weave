from datetime import UTC, datetime, timedelta
from typing import Any

import agent.copilot.tools as tools


class Context:
    def __init__(self, principal: str | None) -> None:
        self.state = {"copilot_principal": principal} if principal else {}


class Store:
    def __init__(self) -> None:
        today = datetime.now(UTC).date()
        self.rows = [
            {
                "commitment_id": "overdue",
                "owner_email": "owner@example.com",
                "title": "Overdue",
                "status": "open",
                "deadline": (today - timedelta(days=2)).isoformat(),
                "last_mentioned": today.isoformat(),
                "mention_count": 1,
                "blocked_by": [],
            },
            {
                "commitment_id": "blocker",
                "owner_email": "owner@example.com",
                "title": "Unblocks work",
                "status": "open",
                "last_mentioned": today.isoformat(),
                "mention_count": 2,
                "blocked_by": [],
            },
            {
                "commitment_id": "dependent",
                "owner_email": "owner@example.com",
                "title": "Dependent",
                "status": "open",
                "last_mentioned": today.isoformat(),
                "mention_count": 1,
                "blocked_by": ["blocker"],
                "blocked_by_evidence": {"blocker": "meeting--0"},
            },
        ]
        self.closed: list[tuple[str, str]] = []

    def list_commitments(self, owner: str, status: str = "") -> list[dict[str, Any]]:
        assert owner == "owner@example.com"
        return [dict(row) for row in self.rows if not status or row["status"] == status]

    def get_commitment(self, owner: str, item_id: str) -> dict[str, Any] | None:
        return next((dict(row) for row in self.rows if row["commitment_id"] == item_id), None)

    def close(self, owner: str, item_id: str) -> bool:
        self.closed.append((owner, item_id))
        return item_id in {row["commitment_id"] for row in self.rows}

    def mention_excerpt(self, item_id: str, mention_ref: str) -> str:
        assert item_id == "dependent"
        assert mention_ref == "meeting--0"
        return "Dependent cannot start until blocker is complete"


def test_tools_refuse_without_session_principal(monkeypatch: Any) -> None:
    monkeypatch.setattr(tools, "_store", lambda: Store())
    assert tools.list_my_commitments("", Context(None)) == []  # type: ignore[arg-type]
    assert tools.trace_blockers("dependent", Context(None)) == []  # type: ignore[arg-type]
    assert tools.close_commitment("overdue", Context(None))["updated"] is False  # type: ignore[arg-type]


def test_attention_order_and_blocker_trace_are_deterministic(monkeypatch: Any) -> None:
    store = Store()
    monkeypatch.setattr(tools, "_store", lambda: store)
    context = Context("owner@example.com")
    rows = tools.list_my_commitments("", context)  # type: ignore[arg-type]
    assert [row["commitment_id"] for row in rows] == ["overdue", "blocker", "dependent"]
    trace = tools.trace_blockers("dependent", context)  # type: ignore[arg-type]
    assert trace[0]["blocker"]["commitment_id"] == "blocker"
    assert trace[0]["evidence_mention_ref"] == "meeting--0"
    assert trace[0]["evidence_excerpt"] == "Dependent cannot start until blocker is complete"


def test_close_uses_only_principal_from_state(monkeypatch: Any) -> None:
    store = Store()
    monkeypatch.setattr(tools, "_store", lambda: store)
    result = tools.close_commitment("overdue", Context("owner@example.com"))  # type: ignore[arg-type]
    assert result["updated"] is True
    assert store.closed == [("owner@example.com", "overdue")]


def test_all_lists_everything_and_a_bad_filter_is_an_error_not_an_empty_list(
    monkeypatch: Any,
) -> None:
    """The live model sent status_filter="all" and got [] back, so it told the
    user they had no commitments while six sat in the graph. An unusable filter
    has to be distinguishable from a genuinely empty list."""
    monkeypatch.setattr(tools, "_store", lambda: Store())
    context = Context("owner@example.com")

    for spelling in ("all", "ALL", " any ", "*", ""):
        assert len(tools.list_my_commitments(spelling, context)) == 3  # type: ignore[arg-type]

    bad = tools.list_my_commitments("in_progress", context)  # type: ignore[arg-type]
    assert len(bad) == 1
    assert "error" in bad[0]
    assert "all" in bad[0]["valid_status_filter_values"]
