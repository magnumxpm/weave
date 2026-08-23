from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from weave_common import (
    ActionItem,
    ActionType,
    CommitmentStatus,
    ContextMatch,
    EnrichedActionItem,
    EnrichedOwnerBundle,
    MatchType,
    Reference,
    ReferenceStatus,
)
from weave_ingestion.delivery import (
    ChatDeliverer,
    GeminiEnterpriseDeliverer,
    MeetingHeader,
    build_card,
)
from weave_ingestion.firestore_client import OnboardedUser
from weave_ingestion.main import _build_welcome_sender


def bundle(
    *,
    enriched: bool = True,
    deadline: date | None = date(2026, 8, 29),
    title: str | None = "Send the launch report",
    details: str | None = "Send the final report to the launch group.",
):
    item = ActionItem(
        description="Send the launch report",
        action_type=ActionType.TASK,
        status=CommitmentStatus.ACCEPTED,
        owner_email="owner@example.com",
        owner_confidence=0.95,
        commitment_turn_ref=3,
        resolution_turn_ref=4,
        deadline=deadline,
    )
    return EnrichedOwnerBundle(
        owner_email="owner@example.com",
        conference_record_id="conferenceRecords/abc",
        meeting_date=date(2026, 8, 22),
        items=[
            EnrichedActionItem(
                item=item,
                title=title,
                details=details,
                matches=(
                    [
                        ContextMatch(
                            source_name="prior_meetings",
                            match_type=MatchType.EXISTING_PRIOR_ITEM,
                            title="Earlier launch report",
                            snippet="Prior commitment",
                        )
                    ]
                    if enriched
                    else []
                ),
            )
        ],
        enriched=enriched,
        skip_reason=None if enriched else "low_confidence",
    )


def widget_texts(card: dict[str, Any]) -> list[str]:
    return [
        widget["decoratedText"]["text"]
        for section in card["card"]["sections"]
        for widget in section["widgets"]
        if "decoratedText" in widget
    ]


def test_build_card_renders_card_v2_contract() -> None:
    meeting = MeetingHeader(
        title="Weekly support sync",
        started_at=datetime(2026, 8, 22, 22, 2, tzinfo=UTC),
        participant_names=("Srija", "Pritam", "Andrei"),
    )
    card = build_card(bundle(), meeting)
    assert card["cardId"] == "weave-abc"
    assert card["card"]["header"] == {
        "title": "Action items for you",
        "subtitle": "Weekly support sync • 22:02",
    }
    assert widget_texts(card)[0] == "with Srija, Pritam, and 1 more"
    item_section = card["card"]["sections"][1]
    assert item_section["collapsible"] is True
    assert item_section["uncollapsibleWidgetsCount"] == 1
    assert item_section["widgets"][0]["decoratedText"]["text"] == "1. Send the launch report"
    assert item_section["widgets"][1]["decoratedText"]["topLabel"] == "Details"
    assert widget_texts(card)[-1] == "Only visible to you"


def test_retrieved_context_is_never_shown_to_the_reader() -> None:
    card_text = "\n".join(widget_texts(build_card(bundle())))
    assert "Earlier launch report" not in card_text
    assert "prior_meetings" not in card_text


def test_card_falls_back_to_the_extracted_description() -> None:
    card = build_card(bundle(enriched=False, title=None, details=None))
    item_section = card["card"]["sections"][0]
    assert item_section["widgets"][0]["decoratedText"]["text"] == "1. Send the launch report"
    assert all(
        widget.get("decoratedText", {}).get("topLabel") != "Details"
        for widget in item_section["widgets"]
    )


def test_unidentified_mentions_are_only_shown_for_an_untitled_fallback() -> None:
    resolved_card = build_card(bundle())
    unknown = Reference(mention="them", turn_ref=7, status=ReferenceStatus.UNKNOWN)
    original_item = bundle().items[0].item
    unknown_bundle = bundle().model_copy(
        update={
            "items": [
                bundle()
                .items[0]
                .model_copy(
                    update={"item": original_item.model_copy(update={"references": [unknown]})}
                )
            ]
        }
    )

    assert "Unidentified" not in "\n".join(widget_texts(resolved_card))
    fallback = unknown_bundle.model_copy(
        update={
            "items": [unknown_bundle.items[0].model_copy(update={"title": None, "details": None})]
        }
    )
    unidentified = [
        widget["decoratedText"]
        for section in build_card(fallback)["card"]["sections"]
        for widget in section["widgets"]
        if widget.get("decoratedText", {}).get("topLabel") == "Unidentified"
    ]
    assert "Unidentified" not in [
        widget.get("decoratedText", {}).get("topLabel")
        for section in build_card(unknown_bundle)["card"]["sections"]
        for widget in section["widgets"]
    ]
    assert unidentified == [
        {
            "topLabel": "Unidentified",
            "text": '"them" (turn 7) could not be identified from the transcript',
            "wrapText": True,
        }
    ]


def test_deadline_is_omitted_when_absent() -> None:
    card = build_card(bundle(deadline=None))
    labels = [
        widget.get("decoratedText", {}).get("topLabel")
        for section in card["card"]["sections"]
        for widget in section["widgets"]
    ]
    assert "Deadline" not in labels


def test_buttons_have_accessible_labels_and_stable_action_parameters() -> None:
    item_section = build_card(bundle())["card"]["sections"][0]
    buttons = item_section["widgets"][-1]["buttonList"]["buttons"]
    assert [button["altText"] for button in buttons] == ["Accept", "Decline"]
    assert [button["onClick"]["action"]["function"] for button in buttons] == [
        "accept_item",
        "decline_item",
    ]
    assert buttons[0]["onClick"]["action"]["parameters"] == [
        {"key": "conference_id", "value": "abc"},
        {"key": "item_index", "value": "1"},
    ]


def test_header_renders_without_meeting_metadata() -> None:
    assert build_card(bundle())["card"]["header"] == {"title": "Action items for you"}


class Request:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def execute(self) -> dict[str, Any]:
        return self.response


class Messages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Request:
        self.calls.append(kwargs)
        return Request({"name": "spaces/dm/messages/123"})


class Spaces:
    def __init__(self) -> None:
        self.find_calls: list[str] = []
        self.message_service = Messages()

    def findDirectMessage(self, *, name: str) -> Request:  # noqa: N802 - Google API shape
        self.find_calls.append(name)
        return Request({"name": "spaces/dm"})

    def messages(self) -> Messages:
        return self.message_service


class ChatClient:
    def __init__(self) -> None:
        self.space_service = Spaces()

    def spaces(self) -> Spaces:
        return self.space_service


def test_chat_deliverer_sends_exactly_one_owner_card() -> None:
    client = ChatClient()
    deliverer = ChatDeliverer(client, lambda email: "users/directory-id")
    delivery_id = deliverer.deliver("OWNER@example.com", bundle())
    assert delivery_id == "spaces/dm/messages/123"
    assert client.space_service.find_calls == ["users/directory-id"]
    assert len(client.space_service.message_service.calls) == 1
    call = client.space_service.message_service.calls[0]
    assert call["parent"] == "spaces/dm"
    assert call["body"] == {"cardsV2": [build_card(bundle())]}


def test_gemini_enterprise_is_explicitly_unimplemented() -> None:
    with pytest.raises(NotImplementedError, match="unverified"):
        GeminiEnterpriseDeliverer().deliver("owner@example.com", bundle())


class DirectoryStub:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.lookups: list[str] = []

    def user_id_for_email(self, email: str) -> str:
        self.lookups.append(email)
        return self.mapping[email]


def test_chat_targets_numeric_id_not_email() -> None:
    # Chat accepts an email alias only under end-user auth; this service uses
    # app auth, where only the numeric id resolves.
    client = ChatClient()
    directory = DirectoryStub({"owner@example.com": "112655489411114378906"})
    deliverer = ChatDeliverer(client, lambda email: f"users/{directory.user_id_for_email(email)}")
    deliverer.deliver("owner@example.com", bundle())

    assert directory.lookups == ["owner@example.com"]
    assert client.space_service.find_calls == ["users/112655489411114378906"]


def test_chat_uses_stored_dm_space_without_identity_lookup() -> None:
    client = ChatClient()
    lookups: list[str] = []
    deliverer = ChatDeliverer(client, lambda email: lookups.append(email) or "users/unexpected")
    target = OnboardedUser(
        user_id="112655489411114378906",
        email="owner@example.com",
        dm_space="spaces/stored-dm",
    )

    deliverer.deliver("owner@example.com", bundle(), target)

    assert lookups == []
    assert client.space_service.find_calls == []
    assert client.space_service.message_service.calls[0]["parent"] == "spaces/stored-dm"


def test_chat_refuses_a_target_for_another_owner() -> None:
    client = ChatClient()
    deliverer = ChatDeliverer(client, lambda email: "users/unexpected")
    target = OnboardedUser(
        user_id="123",
        email="someone-else@example.com",
        dm_space="spaces/wrong",
    )
    with pytest.raises(ValueError, match="target"):
        deliverer.deliver("owner@example.com", bundle(), target)


def test_welcome_uses_stable_idempotency_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ChatClient()
    monkeypatch.setattr("weave_ingestion.main._build_chat_client", lambda: client)
    sender = _build_welcome_sender()
    target = OnboardedUser(user_id="123", email="owner@example.com", dm_space="spaces/stored-dm")
    sender(target)
    sender(target)

    first, second = client.space_service.message_service.calls
    assert first["messageId"] == "client-weave-welcome-v1"
    assert first["requestId"] == second["requestId"]
    assert first["parent"] == "spaces/stored-dm"
