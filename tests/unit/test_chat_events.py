from weave_ingestion.chat_events import ChatEvent, parse_chat_event


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
