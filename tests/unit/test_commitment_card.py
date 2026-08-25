from datetime import date

from weave_common import build_views
from weave_ingestion.copilot_client import CopilotAnswer, ToolResult
from weave_ingestion.delivery.chat_text import to_chat_text
from weave_ingestion.delivery.commitment_card import (
    MAX_CARD_ITEMS,
    build_card_from_rows,
    build_commitment_card,
    rendered_ids_of,
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


def sections(card: dict) -> list[dict]:
    return card["card"]["sections"]


def widget_texts(section: dict) -> list[tuple[str, str]]:
    return [
        (widget["decoratedText"].get("topLabel", ""), widget["decoratedText"]["text"])
        for widget in section["widgets"]
        if "decoratedText" in widget
    ]


def test_a_bare_commitment_renders_a_headline_and_a_button_and_nothing_else() -> None:
    """Absent facts must produce no widget at all, not an empty labelled row."""
    card = build_card_from_rows([row("bare")], today=TODAY)
    widgets = sections(card)[0]["widgets"]

    assert len(widgets) == 2  # headline + buttonList
    label, text = widget_texts(sections(card)[0])[0]
    assert text == "Commitment bare"
    assert label == "Open, no deadline set"
    assert "buttonList" in widgets[-1]


def test_each_fact_present_adds_exactly_one_labelled_row() -> None:
    card = build_card_from_rows(
        [
            row(
                "blocked",
                title="Ship the runbook",
                status="waiting",
                waiting_on="the security team",
                last_mentioned="2026-08-04",
                first_seen="2026-06-01",
                mention_count=5,
                blocked_by=["dep"],
            ),
            row("dep", title="Security review"),
        ],
        today=TODAY,
    )
    labels = {label for section in sections(card) for label, _ in widget_texts(section)}
    assert {"Waiting on", "Blocked by", "History"} <= labels

    blocked = next(
        section for section in sections(card) if section["header"] == "Waiting on someone"
    )
    rows_by_label = dict(widget_texts(blocked))
    assert rows_by_label["Waiting on"] == "the security team"
    assert rows_by_label["Blocked by"] == "Security review"
    assert "over" in rows_by_label["History"]


def test_groups_become_sections_in_order_with_only_the_first_expanded() -> None:
    card = build_card_from_rows([row("late", deadline="2026-08-01"), row("fine")], today=TODAY)
    assert [section["header"] for section in sections(card)] == ["Overdue", "In progress"]
    assert "collapsible" not in sections(card)[0]
    assert sections(card)[1]["collapsible"] is True


def test_every_button_carries_the_ids_actually_rendered() -> None:
    rows = [row("a"), row("b"), row("c")]
    card = build_card_from_rows(rows, today=TODAY)
    assert set(rendered_ids_of(card)) == {"a", "b", "c"}

    buttons = [
        button
        for section in sections(card)
        for widget in section["widgets"]
        if "buttonList" in widget
        for button in widget["buttonList"]["buttons"]
    ]
    assert len(buttons) == 3
    assert all(button["text"] == "Mark done" for button in buttons)
    assert all(button["onClick"]["action"]["function"] == "close_commitment" for button in buttons)


def test_a_closed_commitment_offers_reopen_instead_of_mark_done() -> None:
    card = build_card_from_rows([row("shut", status="closed")], today=TODAY)
    button = sections(card)[0]["widgets"][-1]["buttonList"]["buttons"][0]
    assert button["text"] == "Reopen"
    assert button["onClick"]["action"]["function"] == "reopen_commitment"


def test_a_long_list_is_capped_and_says_how_many_it_held_back() -> None:
    rows = [row(f"c{index}") for index in range(MAX_CARD_ITEMS + 7)]
    card = build_card_from_rows(rows, today=TODAY)

    assert len(rendered_ids_of(card)) == MAX_CARD_ITEMS
    overflow = sections(card)[-1]["widgets"][0]["textParagraph"]["text"]
    assert "+7 more" in overflow


def test_card_is_built_from_the_last_listing_not_a_later_tool_call() -> None:
    """Closing an item after listing must not blank the card it was clicked from."""
    answer = CopilotAnswer(
        text="ok",
        tool_results=(
            ToolResult("list_my_commitments", [row("a")]),
            ToolResult("close_commitment", {"updated": True, "commitment_id": "a"}),
        ),
    )
    assert [item["commitment_id"] for item in answer.commitment_rows()] == ["a"]


def test_an_error_row_is_not_mistaken_for_a_commitment_listing() -> None:
    answer = CopilotAnswer(
        text="that filter is not valid",
        tool_results=(ToolResult("list_my_commitments", [{"error": "unknown status_filter"}]),),
    )
    assert answer.commitment_rows() == []


def test_chat_text_uses_the_formatting_chat_actually_renders() -> None:
    converted = to_chat_text("## Overdue\n- **Ship it** now\n- keep `**this**` intact")
    assert converted.splitlines() == [
        "*Overdue*",
        "• *Ship it* now",
        "• keep `**this**` intact",
    ]


def test_views_and_card_agree_on_what_is_shown() -> None:
    rows = [row("a", deadline="2026-08-01"), row("b")]
    views = build_views(rows, today=TODAY)
    card = build_commitment_card(views)
    assert card["card"]["header"]["subtitle"] == "2 open commitments · 1 overdue"
