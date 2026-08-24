from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from weave_common import MatchType
from weave_ingestion.google_sources import GoogleSourceBroker


class ApiCall:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class DriveFiles:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.kwargs: dict[str, Any] = {}

    def list(self, **kwargs: Any) -> ApiCall:
        self.kwargs = kwargs
        return ApiCall({"files": self.rows})


class DriveService:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.resource = DriveFiles(rows)

    def files(self) -> DriveFiles:
        return self.resource


def test_drive_query_is_escaped_and_rows_map_to_document_matches() -> None:
    drive = DriveService(
        [
            {
                "id": "doc-1",
                "name": "O'Brien's launch plan",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-08-21T14:30:00Z",
                "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
            }
        ]
    )
    subjects: list[str] = []
    broker = GoogleSourceBroker(
        lambda subject: subjects.append(subject) or drive,
        lambda subject: None,
    )

    matches = broker.search("google_docs", "the team's \\ launch", "owner@example.com", 7)

    assert subjects == ["owner@example.com"]
    # Every term of a multi-term `fullText contains` must be present, so terms
    # are searched separately. Stopwords never reach the query, and extraction
    # keeps only word characters, so no quote or backslash can reach it either.
    assert drive.resource.kwargs["q"] == (
        "(fullText contains 'team' or fullText contains 'launch')"
        " and trashed=false and mimeType != 'application/vnd.google-apps.folder'"
    )
    assert drive.resource.kwargs["corpora"] == "user"
    assert drive.resource.kwargs["pageSize"] == 40
    assert matches[0].match_type is MatchType.RELATED_DOCUMENT
    assert matches[0].occurred_on == date(2026, 8, 21)
    assert matches[0].score is not None  # title shares "launch" with the query
    assert matches[0].ref == "https://docs.google.com/document/d/doc-1/edit"


def test_body_only_matches_survive_title_ranking_but_rank_below_it() -> None:
    drive = DriveService(
        [
            # Drive's own order is recency; only the second title shares a term.
            {"name": "Notes", "modifiedTime": "2026-08-22T09:00:00Z"},
            {"name": "Launch plan", "modifiedTime": "2026-08-01T09:00:00Z"},
        ]
    )
    broker = GoogleSourceBroker(lambda subject: drive, lambda subject: None)

    matches = broker.search("google_docs", "launch", "owner@example.com", 5)

    # "Notes" matched on body text the API never returns, so it must survive --
    # but unscored and behind the title match.
    assert [(match.title, match.score is None) for match in matches] == [
        ("Launch plan", False),
        ("Notes", True),
    ]


def test_docs_search_without_informative_terms_never_calls_drive() -> None:
    drive = DriveService([{"name": "Anything"}])
    broker = GoogleSourceBroker(lambda subject: drive, lambda subject: None)

    assert broker.search("google_docs", "with the it", "owner@example.com", 5) == []
    assert drive.resource.kwargs == {}


class TaskLists:
    def list(self) -> ApiCall:
        return ApiCall({"items": [{"id": "inbox"}]})


class Tasks:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> ApiCall:
        self.calls.append(kwargs)
        return ApiCall({"items": self.rows})


class TasksService:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.task_resource = Tasks(rows)

    def tasklists(self) -> TaskLists:
        return TaskLists()

    def tasks(self) -> Tasks:
        return self.task_resource


def test_open_tasks_are_ranked_and_due_date_takes_precedence() -> None:
    tasks = TasksService(
        [
            {
                "id": "irrelevant",
                "title": "Book team lunch",
                "updated": "2026-08-23T10:00:00Z",
            },
            {
                "id": "launch",
                "title": "Finish launch readiness report",
                "notes": "Add the support metrics and blockers",
                "due": "2026-08-30T00:00:00Z",
                "updated": "2026-08-22T10:00:00Z",
                "selfLink": "https://tasks.googleapis.com/tasks/v1/lists/inbox/tasks/launch",
            },
        ]
    )
    broker = GoogleSourceBroker(lambda subject: None, lambda subject: tasks)

    matches = broker.search(
        "google_tasks", "prepare launch report with support metrics", "owner@example.com", 3
    )

    assert tasks.task_resource.calls == [
        {
            "tasklist": "inbox",
            "showCompleted": False,
            "showHidden": False,
            "maxResults": 100,
        }
    ]
    assert [match.title for match in matches] == ["Finish launch readiness report"]
    assert matches[0].match_type is MatchType.OPEN_TASK
    assert matches[0].occurred_on == date(2026, 8, 30)
    assert matches[0].score and matches[0].score > 0


def test_unknown_google_source_is_rejected() -> None:
    broker = GoogleSourceBroker(lambda subject: None, lambda subject: None)
    with pytest.raises(ValueError, match="unsupported context source"):
        broker.search("buganizer", "query", "owner@example.com", 5)
