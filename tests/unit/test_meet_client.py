from __future__ import annotations

from typing import Any

import pytest
from weave_ingestion.directory_client import VIEW_TYPE, DirectoryClient
from weave_ingestion.meet_client import LiveMeetArtifactSource

RECORD = {"startTime": "2026-08-22T10:00:00Z"}


class Request:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        return self._response


class Entries:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    def list(self, **_: Any) -> Request:
        return Request({"transcriptEntries": self._entries})


class Transcripts:
    def __init__(self, transcripts: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
        self._transcripts = transcripts
        self._entries = entries

    def list(self, **_: Any) -> Request:
        return Request({"transcripts": self._transcripts})

    def entries(self) -> Entries:
        return Entries(self._entries)


class Participants:
    def __init__(self, participants: list[dict[str, Any]]) -> None:
        self._participants = participants

    def list(self, **_: Any) -> Request:
        return Request({"participants": self._participants})


class ConferenceRecords:
    def __init__(self, participants: list[dict[str, Any]]) -> None:
        self._participants = participants

    def get(self, **_: Any) -> Request:
        return Request(RECORD)

    def participants(self) -> Participants:
        return Participants(self._participants)

    def transcripts(self) -> Transcripts:
        return Transcripts(
            [{"name": "conferenceRecords/c/transcripts/t"}],
            [{"participant": "p1", "text": "hello"}],
        )


class MeetService:
    def __init__(self, participants: list[dict[str, Any]]) -> None:
        self._participants = participants

    def conferenceRecords(self) -> ConferenceRecords:  # noqa: N802 - mirrors the API client
        return ConferenceRecords(self._participants)


def participant(name: str, user_id: str) -> dict[str, Any]:
    return {"name": name, "signedinUser": {"user": f"users/{user_id}", "displayName": name}}


def source(participants: list[dict[str, Any]], resolve: Any) -> LiveMeetArtifactSource:
    return LiveMeetArtifactSource(lambda subject: MeetService(participants), resolve)


def test_live_fetch_requires_a_subject_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="subject"):
        source([], lambda user_id: "x@example.com").fetch("c")


def test_anonymous_participants_are_skipped_without_a_lookup() -> None:
    def resolve(user_id: str) -> str:
        raise AssertionError("anonymous participants must not be looked up")

    request = source([{"name": "p1"}], resolve).fetch("c", subject="admin@example.com")
    assert request.attendees == []


def test_one_unresolvable_guest_does_not_fail_the_meeting() -> None:
    def resolve(user_id: str) -> str:
        if user_id == "guest":
            raise RuntimeError("403 not in this directory")
        return "member@example.com"

    request = source([participant("p1", "member"), participant("p2", "guest")], resolve).fetch(
        "c", subject="admin@example.com"
    )

    assert [attendee.email for attendee in request.attendees] == ["member@example.com"]


def test_every_participant_unresolvable_fails_loudly() -> None:
    # A broken directory must not masquerade as an empty meeting.
    def resolve(user_id: str) -> str:
        raise RuntimeError("403 not authorized")

    with pytest.raises(LookupError, match="directory lookups are failing"):
        source([participant("p1", "a"), participant("p2", "b")], resolve).fetch(
            "c", subject="admin@example.com"
        )


def test_directory_uses_the_admin_view_which_alone_resolves_other_users() -> None:
    seen: dict[str, Any] = {}

    class Users:
        def get(self, **kwargs: Any) -> Request:
            seen.update(kwargs)
            return Request({"primaryEmail": "someone@example.com", "id": "42"})

    class Service:
        def users(self) -> Users:
            return Users()

    client = DirectoryClient(Service())
    assert client.email_for_user_id("101") == "someone@example.com"
    assert seen["viewType"] == VIEW_TYPE == "admin_view"
    assert client.user_id_for_email("someone@example.com") == "42"
    assert seen["viewType"] == "admin_view"
