from weave_ingestion.chat_events import ChatClickEvent, ChatEvent, parse_chat_event


def event(event_type: str = "ADDED_TO_SPACE", *, dm: bool = True) -> dict:
    return {
        "type": event_type,
        "user": {"name": "users/123", "email": "user@example.com"},
        "space": {
            "name": "spaces/dm" if dm else "spaces/group",
            "spaceType": "DIRECT_MESSAGE" if dm else "SPACE",
            "singleUserBotDm": dm,
        },
    }


def test_added_direct_message_parses() -> None:
    assert parse_chat_event(event()) == ChatEvent(
        kind="added",
        user_id="123",
        email="user@example.com",
        space_name="spaces/dm",
    )


def test_removed_direct_message_parses() -> None:
    assert parse_chat_event(event("REMOVED_FROM_SPACE")).kind == "removed"  # type: ignore[union-attr]


def test_group_lifecycle_events_are_ignored() -> None:
    assert parse_chat_event(event(dm=False)) is None
    assert parse_chat_event(event("REMOVED_FROM_SPACE", dm=False)) is None


def test_a_direct_message_onboards_because_dm_installs_never_send_added() -> None:
    # ADDED_TO_SPACE only reaches apps that joined spaces and group
    # conversations; a direct-message-only app sees MESSAGE instead.
    assert parse_chat_event(event("MESSAGE")) == ChatEvent(
        kind="added",
        user_id="123",
        email="user@example.com",
        space_name="spaces/dm",
    )


def test_the_apps_own_messages_never_onboard() -> None:
    payload = event("MESSAGE")
    payload["user"]["type"] = "BOT"
    assert parse_chat_event(payload) is None


def test_unknown_and_garbage_are_ignored() -> None:
    assert parse_chat_event({"type": "FUTURE_EVENT"}) is None
    assert parse_chat_event("garbage") is None


def addon_event(payload_field: str, *, space_beside_payload: bool = True) -> dict:
    # The console configures Chat apps as Workspace add-ons, which nest the
    # interaction under `chat` and identify it by which payload field exists.
    space = {"name": "spaces/dm", "spaceType": "DIRECT_MESSAGE", "type": "DM"}
    chat: dict = {
        "user": {"name": "users/123", "email": "user@example.com", "type": "HUMAN"},
        payload_field: {"message": {"text": "hi"}},
    }
    if space_beside_payload:
        chat["space"] = space
    else:
        chat[payload_field]["space"] = space
    return {"chat": chat, "commonEventObject": {}}


def test_addon_direct_message_onboards() -> None:
    assert parse_chat_event(addon_event("messagePayload")) == ChatEvent(
        kind="added",
        user_id="123",
        email="user@example.com",
        space_name="spaces/dm",
    )


def test_addon_space_may_live_inside_the_payload() -> None:
    parsed = parse_chat_event(addon_event("messagePayload", space_beside_payload=False))
    assert parsed is not None
    assert parsed.space_name == "spaces/dm"


def test_addon_added_and_removed_payloads_map_to_kinds() -> None:
    assert parse_chat_event(addon_event("addedToSpacePayload")).kind == "added"  # type: ignore[union-attr]
    assert parse_chat_event(addon_event("removedFromSpacePayload")).kind == "removed"  # type: ignore[union-attr]


def test_addon_envelope_without_a_known_payload_is_ignored() -> None:
    assert (
        parse_chat_event({"chat": {"user": {"name": "users/1"}}, "commonEventObject": {}}) is None
    )
    assert parse_chat_event(addon_event("buttonClickedPayload")) is None


def test_user_name_must_contain_numeric_identity() -> None:
    payload = event()
    payload["user"]["name"] = "users/not-numeric"
    assert parse_chat_event(payload) is None


def test_email_can_be_absent_for_directory_fallback() -> None:
    payload = event()
    payload["user"].pop("email")
    parsed = parse_chat_event(payload)
    assert parsed is not None
    assert parsed.email is None


def test_classic_card_click_parses_without_becoming_onboarding() -> None:
    payload = event("CARD_CLICKED")
    payload["action"] = {
        "actionMethodName": "accept_item",
        "parameters": [
            {"key": "conference_id", "value": "abc"},
            {"key": "item_index", "value": "2"},
        ],
    }
    assert parse_chat_event(payload) == ChatClickEvent(
        function="accept_item",
        conference_id="abc",
        item_index="2",
        user_id="123",
    )


def test_addon_card_click_reads_common_event_parameters() -> None:
    payload = addon_event("buttonClickedPayload")
    payload["commonEventObject"] = {
        "invokedFunction": "decline_item",
        "parameters": {"conference_id": "abc", "item_index": "1"},
    }
    assert parse_chat_event(payload) == ChatClickEvent(
        function="decline_item",
        conference_id="abc",
        item_index="1",
        user_id="123",
    )
