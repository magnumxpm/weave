from datetime import date

from weave_common import (
    CommitmentView,
    UrgencyGroup,
    build_views,
    decorate_rows,
    group_views,
    summarize,
    transitive_dependents,
)

TODAY = date(2026, 8, 25)


def row(commitment_id: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "commitment_id": commitment_id,
        "title": f"Commitment {commitment_id}",
        "status": "open",
        "mention_count": 1,
        "last_mentioned": "2026-08-24",
        "first_seen": "2026-08-24",
        "blocked_by": [],
    }
    return base | overrides


def only(views: list[CommitmentView], commitment_id: str) -> CommitmentView:
    return next(view for view in views if view.commitment_id == commitment_id)


def test_each_group_states_a_reason_built_only_from_facts_the_row_carries() -> None:
    rows = [
        row("overdue", deadline="2026-08-22"),
        row("soon", deadline="2026-08-26"),
        row(
            "waiting", status="waiting", waiting_on="the security team", last_mentioned="2026-08-04"
        ),
        row("quiet", last_mentioned="2026-08-01"),
        row("plain"),
        row(
            "done",
            status="likely_complete",
            status_confidence=0.92,
            status_evidence="dashboard discussed as existing",
        ),
    ]
    views = build_views(rows, today=TODAY)

    assert only(views, "overdue").urgency is UrgencyGroup.OVERDUE
    assert only(views, "overdue").reason == "Overdue by 3 days"
    assert only(views, "soon").reason == "Due in 1 day"
    assert only(views, "waiting").reason == "Waiting on the security team for 3 weeks"
    assert only(views, "quiet").urgency is UrgencyGroup.STALE
    assert only(views, "done").reason == "Looks done (92% confident)"

    # A row with no deadline must never be described in deadline terms.
    plain = only(views, "plain")
    assert plain.urgency is UrgencyGroup.ACTIVE
    assert "overdue" not in plain.reason.lower() and "due" not in plain.reason.lower()
    assert plain.deadline is None


def test_absent_facts_are_absent_rather_than_empty() -> None:
    """`present_facts` is what lets a renderer omit a row instead of drawing a blank."""
    bare = only(build_views([row("bare")], today=TODAY), "bare")
    assert bare.present_facts == frozenset()
    assert bare.waiting_on is None and bare.carry_over is None
    assert bare.blocked_by_titles == () and bare.status_evidence is None

    rich = only(
        build_views(
            [
                row(
                    "rich",
                    mention_count=4,
                    first_seen="2026-07-01",
                    waiting_on="Sarah",
                    status="waiting",
                    deadline="2026-09-01",
                )
            ],
            today=TODAY,
        ),
        "rich",
    )
    assert {"waiting_on", "carry_over", "deadline"} <= rich.present_facts
    assert rich.carry_over == "Raised in 4 meetings over 7 weeks"


def test_evidence_is_kept_only_where_it_means_something() -> None:
    """status_evidence explains a likely_complete guess; on an open row it would mislead."""
    rows = [
        row("open_one", status="open", status_evidence="stale note", status_confidence=0.9),
        row(
            "guessed",
            status="likely_complete",
            status_evidence="shipped last week",
            status_confidence=0.8,
        ),
    ]
    views = build_views(rows, today=TODAY)
    assert only(views, "open_one").status_evidence is None
    assert only(views, "open_one").status_confidence is None
    assert only(views, "guessed").status_evidence == "shipped last week"


def test_unblock_impact_is_counted_transitively_and_lifts_the_score() -> None:
    rows = [
        row("blocker"),
        row("a", blocked_by=["blocker"]),
        row("b", blocked_by=["a"]),
        row("idle"),
        row("overdue", deadline="2026-08-24"),
    ]
    views = build_views(rows, today=TODAY)

    blocker = only(views, "blocker")
    assert blocker.open_dependents == 2  # transitive: a, and b behind a
    assert blocker.urgency is UrgencyGroup.BLOCKING
    assert blocker.reason == "Holding up 2 other commitments"
    assert blocker.attention_score > only(views, "idle").attention_score

    # A broken promise still outweighs unblock impact at this ratio; the group
    # ordering, not the score, is what keeps blockers visible.
    assert only(views, "overdue").attention_score > blocker.attention_score


def test_blocker_titles_resolve_and_unknown_ids_are_dropped_not_shown_raw() -> None:
    rows = [row("known", title="Security review"), row("child", blocked_by=["known", "ghost"])]
    child = only(build_views(rows, today=TODAY), "child")
    assert child.blocked_by_titles == ("Security review",)


def test_dependent_counting_survives_a_cycle_and_ignores_closed_rows() -> None:
    cyclic = [row("x", blocked_by=["y"]), row("y", blocked_by=["x"])]
    assert transitive_dependents(cyclic) == {"x": 1, "y": 1}

    with_closed = [row("open_root"), row("shut", status="closed", blocked_by=["open_root"])]
    assert transitive_dependents(with_closed)["open_root"] == 0


def test_groups_render_in_declared_order_and_empty_ones_disappear() -> None:
    rows = [row("late", deadline="2026-08-01"), row("fine")]
    buckets = group_views(build_views(rows, today=TODAY))
    assert [bucket.group for bucket in buckets] == [UrgencyGroup.OVERDUE, UrgencyGroup.ACTIVE]
    assert all(bucket.views for bucket in buckets)


def test_decorate_needs_the_full_set_to_score_a_filtered_slice() -> None:
    """Unblock impact is invisible from a slice, so the slice is scored against everything."""
    everything = [row("blocker"), row("dependent", blocked_by=["blocker"])]
    slice_only = [row("blocker")]

    unaware = decorate_rows(slice_only, today=TODAY)
    aware = decorate_rows(slice_only, all_rows=everything, today=TODAY)
    assert unaware[0]["open_dependents"] == 0
    assert aware[0]["open_dependents"] == 1
    assert aware[0]["attention_score"] > unaware[0]["attention_score"]


def test_summary_counts_only_what_is_there() -> None:
    assert summarize([]) == "Nothing open"
    views = build_views([row("a", deadline="2026-08-01"), row("b")], today=TODAY)
    assert summarize(views) == "2 open commitments · 1 overdue"


def test_carry_over_is_dropped_when_it_only_restates_the_reason() -> None:
    """Both surfaces showed "Raised in 2 meetings" twice for the same commitment."""
    same_day = only(
        build_views(
            [row("same", mention_count=2, first_seen="2026-08-24", last_mentioned="2026-08-24")],
            today=TODAY,
        ),
        "same",
    )
    assert same_day.reason == "Raised in 2 meetings"
    assert same_day.carry_over is None

    spanning = only(
        build_views(
            [row("span", mention_count=2, first_seen="2026-07-01", last_mentioned="2026-08-24")],
            today=TODAY,
        ),
        "span",
    )
    assert spanning.carry_over == "Raised in 2 meetings over 7 weeks"
    assert spanning.carry_over != spanning.reason
