from __future__ import annotations

from datetime import date
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
)
from weave_ingestion.delivery import (
    ChatDeliverer,
    GeminiEnterpriseDeliverer,
    build_card,
)


def bundle(*, enriched: bool = True, deadline: date | None = date(2026, 8, 29)):
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
    ]


def test_build_card_renders_card_v2_contract() -> None:
    card = build_card(bundle())
    assert card["cardId"] == "weave-abc"
    assert card["card"]["header"]["title"] == "Your action items from 2026-08-22"
    assert len(card["card"]["sections"]) == 1
    assert "3" in widget_texts(card)
    assert "prior_meetings: Earlier launch report" in widget_texts(card)


def test_unenriched_and_no_match_states_are_distinct() -> None:
    unavailable = widget_texts(build_card(bundle(enriched=False)))
    no_matches_bundle = bundle().model_copy(
        update={"items": [bundle().items[0].model_copy(update={"matches": []})]}
    )
    no_matches = widget_texts(build_card(no_matches_bundle))
    assert "Related context unavailable" in unavailable
    assert "No related context found" in no_matches


def test_deadline_is_omitted_when_absent() -> None:
    card = build_card(bundle(deadline=None))
    labels = [
        widget["decoratedText"]["topLabel"]
        for section in card["card"]["sections"]
        for widget in section["widgets"]
    ]
    assert "Deadline" not in labels


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
