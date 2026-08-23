"""Meet artifact retrieval behind a seam: live Workspace APIs or local fixtures."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from weave_common import Attendee, PipelineRequest, TranscriptTurn

logger = logging.getLogger(__name__)

_CONFERENCE_ID = re.compile(r"conferenceRecords/([A-Za-z0-9_-]+)")
_SUBSCRIBER_ID = re.compile(r"cloudidentity\.googleapis\.com/users/(\d+)")


def extract_conference_id(payload: str) -> str | None:
    """Pull the conference record id out of a Workspace Events payload."""
    match = _CONFERENCE_ID.search(payload)
    return match.group(1) if match else None


def extract_subscriber_user_id(attributes: dict[str, str], payload: str) -> str | None:
    """Numeric id of the user whose subscription produced this event.

    Conference records are only visible to participants, so the Meet fetch must
    impersonate this user rather than one fixed account. Live events carry it in
    `ce-subject` as `//cloudidentity.googleapis.com/users/{id}` (`ce-source`
    names the subscription, not the user); the other keys and the payload are
    searched as fallbacks in case the envelope changes.
    """
    for key in ("ce-subject", "subject", "ce-source", "source"):
        if (value := attributes.get(key)) and (match := _SUBSCRIBER_ID.search(value)):
            return match.group(1)
    match = _SUBSCRIBER_ID.search(payload)
    return match.group(1) if match else None


class MeetArtifactSource(ABC):
    @abstractmethod
    def fetch(self, conference_id: str, subject: str | None = None) -> PipelineRequest:
        """Return the pipeline request, reading as `subject` when impersonating."""


class FixtureMeetArtifactSource(MeetArtifactSource):
    """Reads `{fixture_dir}/{conference_id}.json` shaped like PipelineRequest.

    This is what `make smoke` exercises: the whole pipeline runs for real with
    only the Workspace boundary replaced.
    """

    def __init__(self, fixture_dir: str) -> None:
        self._dir = Path(fixture_dir)

    def fetch(self, conference_id: str, subject: str | None = None) -> PipelineRequest:
        del subject  # fixtures are not user-scoped
        path = self._dir / f"{conference_id}.json"
        return PipelineRequest.model_validate_json(path.read_text(encoding="utf-8"))


class LiveMeetArtifactSource(MeetArtifactSource):
    """Meet REST API v2 with delegated user credentials; full pagination.

    Identity is deterministic: transcript entries carry `participant`, and the
    participant's signed-in user id resolves to an email via the Directory API.

    A Meet service is built per subject because conference records are visible
    only to that conference's participants; one shared client would restrict the
    system to a single user's meetings.
    """

    def __init__(
        self,
        build_meet_service: Any,
        resolve_email: Any,
        build_drive_service: Any | None = None,
    ) -> None:
        self._build_meet_service = build_meet_service
        self._build_drive_service = build_drive_service
        self._resolve_email = resolve_email
        self._meet: Any = None
        self._drive: Any = None

    def _paginate(self, request_fn: Any, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            response = request_fn(pageToken=token, **kwargs).execute()
            items.extend(response.get(key, []))
            token = response.get("nextPageToken")
            if not token:
                return items

    def _meeting_title(self, document_id: str | None) -> str | None:
        """Read the agenda-like title from the transcript Drive document."""
        if not document_id or self._drive is None:
            return None
        try:
            response = (
                self._drive.files()
                .get(
                    fileId=document_id.rsplit("/", 1)[-1],
                    fields="name",
                    supportsAllDrives=True,
                )
                .execute()
            )
            name = str(response.get("name") or "").removesuffix(" - Transcript")
            return name.split(" (", 1)[0].strip() or None
        except Exception:  # noqa: BLE001 - the agenda must never fail a meeting
            logger.exception("transcript document title unavailable")
            return None

    def fetch(self, conference_id: str, subject: str | None = None) -> PipelineRequest:
        if not subject:
            raise ValueError("live Meet reads require a subject to impersonate")
        self._meet = self._build_meet_service(subject)
        self._drive = self._build_drive_service(subject) if self._build_drive_service else None
        record_name = f"conferenceRecords/{conference_id}"
        record = self._meet.conferenceRecords().get(name=record_name).execute()
        started_at = datetime.fromisoformat(record["startTime"].replace("Z", "+00:00"))
        meeting_date = started_at.date()

        participants = self._paginate(
            self._meet.conferenceRecords().participants().list, "participants", parent=record_name
        )
        attendees: list[Attendee] = []
        participant_names: dict[str, Attendee] = {}
        signed_in_count = 0
        for participant in participants:
            signed_in = participant.get("signedinUser")
            if not signed_in:
                continue  # anonymous/phone participants can never own an item
            signed_in_count += 1
            try:
                email = self._resolve_email(signed_in["user"].removeprefix("users/"))
            except Exception:  # noqa: BLE001 - one guest must not fail the meeting
                # Visitors from outside the directory cannot be resolved and so
                # can never own an item. Drop the person, not the meeting; a
                # systemic lookup failure is still caught below.
                logger.warning(
                    "unresolved participant dropped",
                    extra={
                        "conference_id": conference_id,
                        "participant_id": participant.get("name"),
                    },
                )
                continue
            attendee = Attendee(
                email=email,
                participant_id=participant["name"],
                display_name=signed_in.get("displayName", email),
            )
            attendees.append(attendee)
            participant_names[participant["name"]] = attendee

        if signed_in_count and not attendees:
            # Every signed-in participant failed to resolve: that is a broken
            # directory, not a room full of guests. Fail loudly and retry.
            raise LookupError(
                f"none of the {signed_in_count} signed-in participants of {record_name} "
                "could be resolved; directory lookups are failing"
            )

        transcripts = self._paginate(
            self._meet.conferenceRecords().transcripts().list, "transcripts", parent=record_name
        )
        if not transcripts:
            raise LookupError(f"no transcript for {record_name}")
        document_id = (transcripts[0].get("docsDestination") or {}).get("document")
        meeting_title = self._meeting_title(document_id)
        entries = self._paginate(
            self._meet.conferenceRecords().transcripts().entries().list,
            "transcriptEntries",
            parent=transcripts[0]["name"],
        )

        turns = []
        for index, entry in enumerate(entries):
            participant_id = entry.get("participant")
            attendee = participant_names.get(participant_id) if participant_id else None
            turns.append(
                TranscriptTurn(
                    turn_index=index,
                    participant_id=participant_id,
                    speaker_name=attendee.display_name if attendee else "Unknown speaker",
                    text=entry.get("text", ""),
                )
            )
        logger.info(
            "fetched meet artifacts",
            extra={"conference_id": conference_id, "turn_count": len(turns)},
        )
        return PipelineRequest(
            transcript_turns=turns,
            conference_record_id=record_name,
            meeting_date=meeting_date,
            attendees=attendees,
            meeting_title=meeting_title,
            started_at=started_at,
        )
