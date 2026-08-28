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
    def __init__(
        self,
        participants: list[dict[str, Any]],
        docs_destination: dict[str, str] | None = None,
    ) -> None:
        self._participants = participants
        self._docs_destination = docs_destination

    def get(self, **_: Any) -> Request:
        return Request(RECORD)

    def participants(self) -> Participants:
        return Participants(self._participants)

    def transcripts(self) -> Transcripts:
        transcript: dict[str, Any] = {"name": "conferenceRecords/c/transcripts/t"}
        if self._docs_destination is not None:
            transcript["docsDestination"] = self._docs_destination
        return Transcripts(
            [transcript],
            [{"participant": "p1", "text": "hello"}],
        )


class MeetService:
    def __init__(
        self,
        participants: list[dict[str, Any]],
        docs_destination: dict[str, str] | None = None,
    ) -> None:
        self._participants = participants
        self._docs_destination = docs_destination

    def conferenceRecords(self) -> ConferenceRecords:  # noqa: N802 - mirrors the API client
        return ConferenceRecords(self._participants, self._docs_destination)


class DriveFiles:
    def __init__(self, name: str | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> Request:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return Request({"name": self.name})


class DriveService:
    def __init__(self, files: DriveFiles) -> None:
        self.file_service = files

    def files(self) -> DriveFiles:
        return self.file_service


def participant(name: str, user_id: str) -> dict[str, Any]:
    return {"name": name, "signedinUser": {"user": f"users/{user_id}", "displayName": name}}


def source(
    participants: list[dict[str, Any]],
    resolve: Any,
    *,
    docs_destination: dict[str, str] | None = None,
    drive: DriveService | None = None,
    workspace_timezone: str = "UTC",
) -> LiveMeetArtifactSource:
    return LiveMeetArtifactSource(
        lambda subject: MeetService(participants, docs_destination),
        resolve,
        (lambda subject: drive) if drive is not None else None,
        workspace_timezone=workspace_timezone,
    )


def test_live_fetch_requires_a_subject_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="subject"):
        source([], lambda user_id: "x@example.com").fetch("c")


def test_meeting_date_uses_the_configured_workspace_timezone(monkeypatch: Any) -> None:
    monkeypatch.setitem(RECORD, "startTime", "2026-08-22T23:30:00Z")
    request = source(
        [], lambda user_id: "unused@example.com", workspace_timezone="Asia/Kolkata"
    ).fetch("c", subject="admin@example.com")
    assert request.meeting_date.isoformat() == "2026-08-23"


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


@pytest.mark.parametrize(
    ("document_name", "expected"),
    [
        ("Weekly support sync (2026-08-23 at 22:02 GMT+5:30) - Transcript", "Weekly support sync"),
        ("Weekly support sync - Transcript", "Weekly support sync"),
        (" (2026-08-23 at 22:02 GMT+5:30) - Transcript", None),
    ],
)
def test_meeting_title_is_parsed_from_the_transcript_document(
    document_name: str, expected: str | None
) -> None:
    files = DriveFiles(name=document_name)
    request = source(
        [],
        lambda user_id: "unused@example.com",
        docs_destination={"document": "documents/drive-file-id"},
        drive=DriveService(files),
    ).fetch("c", subject="admin@example.com")

    assert request.meeting_title == expected
    assert request.started_at is not None
    assert files.calls == [{"fileId": "drive-file-id", "fields": "name", "supportsAllDrives": True}]


def test_drive_failure_omits_the_title_without_losing_the_meeting() -> None:
    files = DriveFiles(error=RuntimeError("forbidden"))
    request = source(
        [],
        lambda user_id: "unused@example.com",
        docs_destination={"document": "drive-file-id"},
        drive=DriveService(files),
    ).fetch("c", subject="admin@example.com")
    assert request.meeting_title is None
    assert request.conference_record_id == "conferenceRecords/c"


def test_drive_is_not_called_without_a_docs_destination() -> None:
    files = DriveFiles(name="Must not be read")
    request = source(
        [],
        lambda user_id: "unused@example.com",
        drive=DriveService(files),
    ).fetch("c", subject="admin@example.com")
    assert request.meeting_title is None
    assert files.calls == []
