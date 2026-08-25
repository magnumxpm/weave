"""Render commitment views as a Google Chat card.

Deterministic: every line comes from a `CommitmentView` field, so the card can
never assert something the record does not carry. A fact that is absent produces
no widget at all rather than an empty row -- which is why the view model uses
None for "not applicable" instead of a placeholder string.
"""

from __future__ import annotations

from typing import Any

from weave_common import CommitmentView, UrgencyGroup, build_views, group_views, summarize

# Chat truncates very tall cards and every rendered id rides along in each
# button's parameters, so cap what one card carries and offer the rest in words.
MAX_CARD_ITEMS = 10


def _decorated(text: str, *, top_label: str, icon: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"text": text, "topLabel": top_label, "wrapText": True}
    if icon:
        value["startIcon"] = {"materialIcon": {"name": icon}}
    return {"decoratedText": value}


def _action(
    function: str, commitment_id: str, rendered_ids: str, button_url: str
) -> dict[str, Any]:
    # HTTP-deployed add-on apps route the click to onClick.action.function, so
    # it must be the endpoint URL there; the logical action is weave_action.
    return {
        "function": button_url or function,
        "parameters": [
            {"key": "weave_action", "value": function},
            {"key": "commitment_id", "value": commitment_id},
            {"key": "rendered_ids", "value": rendered_ids},
        ],
    }


def _item_widgets(
    view: CommitmentView, rendered_ids: str, *, advise: bool = False, button_url: str = ""
) -> list[dict[str, Any]]:
    """One commitment: the headline, then only the facts it actually has."""
    widgets: list[dict[str, Any]] = [_decorated(view.title, top_label=view.reason, icon=view.icon)]
    if advise and view.recommendation:
        widgets.append(_decorated(view.recommendation, top_label="Suggested next step"))
    facts = view.present_facts
    if "waiting_on" in facts:
        widgets.append(_decorated(str(view.waiting_on), top_label="Waiting on"))
    if "blocked_by" in facts:
        widgets.append(_decorated(", ".join(view.blocked_by_titles), top_label="Blocked by"))
    if "open_dependents" in facts:
        noun = "commitment" if view.open_dependents == 1 else "commitments"
        widgets.append(_decorated(f"{view.open_dependents} other {noun}", top_label="Unblocks"))
    if "carry_over" in facts:
        widgets.append(_decorated(str(view.carry_over), top_label="History"))
    if "status_evidence" in facts:
        widgets.append(_decorated(str(view.status_evidence), top_label="Evidence"))

    closed = view.status == "closed"
    widgets.append(
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "Reopen" if closed else "Mark done",
                        "icon": {"materialIcon": {"name": "undo" if closed else "check_circle"}},
                        "onClick": {
                            "action": _action(
                                "reopen_commitment" if closed else "close_commitment",
                                view.commitment_id,
                                rendered_ids,
                                button_url,
                            )
                        },
                    }
                ]
            }
        }
    )
    return widgets


def build_commitment_card(
    views: list[CommitmentView],
    *,
    title: str = "Your commitments",
    advise: bool = False,
    button_url: str = "",
) -> dict[str, Any]:
    """Render grouped commitments; the most urgent group is the one left open.

    `advise` turns a listing into a recommendation: each item also carries its
    suggested next step. Kept off for a full list, where ten repetitions of
    advice would bury the list it is advising on.
    """
    shown = views[:MAX_CARD_ITEMS]
    rendered_ids = ",".join(view.commitment_id for view in shown)
    sections: list[dict[str, Any]] = []

    for position, bucket in enumerate(group_views(shown)):
        widgets: list[dict[str, Any]] = []
        for view in bucket.views:
            widgets.extend(_item_widgets(view, rendered_ids, advise=advise, button_url=button_url))
        section: dict[str, Any] = {"header": bucket.label, "widgets": widgets}
        if position:
            # Everything below the leading group folds away, so a long list stays
            # scannable while the group that needs action is already open.
            section["collapsible"] = True
            section["uncollapsibleWidgetsCount"] = 1
        sections.append(section)

    if len(views) > MAX_CARD_ITEMS:
        remaining = len(views) - MAX_CARD_ITEMS
        sections.append(
            {
                "widgets": [
                    {"textParagraph": {"text": f"+{remaining} more — ask me to list them all."}}
                ]
            }
        )

    return {
        "cardId": "weave-commitments",
        "card": {
            "header": {"title": title, "subtitle": summarize(views)},
            "sections": sections,
        },
    }


def build_card_from_rows(
    rows: list[dict[str, Any]],
    *,
    today: Any,
    title: str = "Your commitments",
    advise: bool = False,
    button_url: str = "",
) -> dict[str, Any]:
    """Convenience for callers holding raw tool rows rather than views."""
    return build_commitment_card(
        build_views(rows, today=today), title=title, advise=advise, button_url=button_url
    )


def lead_line(views: list[CommitmentView], *, advise: bool) -> str:
    """The one sentence that goes above a card.

    The model's own prose is dropped on this surface: it restates every item the
    card is about to draw, which reads as the same answer printed twice. Gemini
    Enterprise still gets the full prose, because there it is the whole answer.
    """
    if not views:
        return "Nothing needs your attention right now."
    if advise:
        top = views[0]
        # Never case-fold the recommendation into the sentence: it carries
        # product and person names ("OAuth", "Sarah") that lowercasing mangles.
        opener = f"Start with *{top.title}*. {top.recommendation}."
        if len(views) == 1:
            return opener
        return f"{opener} {len(views) - 1} more below, in the order I'd take them."
    return f"Everything on your plate — {summarize(views)}."


def rendered_ids_of(card: dict[str, Any]) -> tuple[str, ...]:
    """Ids a card's buttons carry, for tests and for re-render round-tripping."""
    for section in card.get("card", {}).get("sections", []):
        for widget in section.get("widgets", []):
            for button in widget.get("buttonList", {}).get("buttons", []):
                for parameter in button["onClick"]["action"]["parameters"]:
                    if parameter["key"] == "rendered_ids" and parameter["value"]:
                        return tuple(parameter["value"].split(","))
    return ()


__all__ = [
    "MAX_CARD_ITEMS",
    "build_card_from_rows",
    "build_commitment_card",
    "rendered_ids_of",
    "UrgencyGroup",
]
