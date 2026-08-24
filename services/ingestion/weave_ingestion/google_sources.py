"""Delegated Google-source searches for the authenticated context broker."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from weave_common import ContextMatch, MatchType, rank, terms

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"

MAX_LIMIT = 20
MAX_QUERY_CHARS = 400
TASK_CANDIDATES = 100
# Drive ranks nothing for us and returns no snippet, so ask for a window wide
# enough that local ranking has something to choose between.
DRIVE_CANDIDATES = 40
# Every term of a multi-term `fullText contains` must appear in the file, so a
# sentence-shaped query matches nothing. Searching each term separately is what
# makes the source return anything at all. The cap bounds the size of the
# generated query only -- it is not a relevance judgment, so terms keep their
# order in the description rather than being sorted by any proxy for specificity.
DRIVE_QUERY_TERMS = 12
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def _escape_drive_query(text: str) -> str:
    """Escape the two special characters accepted inside Drive query literals."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _date_from_api(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


class GoogleSourceBroker:
    """Search Google Workspace APIs as one explicitly supplied user."""

    def __init__(
        self,
        build_drive_service: Callable[[str], Any],
        build_tasks_service: Callable[[str], Any],
    ) -> None:
        self._build_drive_service = build_drive_service
        self._build_tasks_service = build_tasks_service

    def search(self, source: str, query: str, subject: str, limit: int) -> list[ContextMatch]:
        limit = max(1, min(limit, MAX_LIMIT))
        query = query[:MAX_QUERY_CHARS]
        if source == "google_docs":
            return self._search_docs(subject, query, limit)
        if source == "google_tasks":
            return self._search_tasks(subject, query, limit)
        raise ValueError(f"unsupported context source: {source}")

    def _search_docs(self, subject: str, query: str, limit: int) -> list[ContextMatch]:
        query_terms = terms(query)[:DRIVE_QUERY_TERMS]
        if not query_terms:
            return []
        clauses = " or ".join(
            f"fullText contains '{_escape_drive_query(term)}'" for term in query_terms
        )
        service = self._build_drive_service(subject)
        response = (
            service.files()
            .list(
                q=f"({clauses}) and trashed=false and mimeType != '{FOLDER_MIME_TYPE}'",
                corpora="user",
                orderBy="modifiedTime desc",
                pageSize=DRIVE_CANDIDATES,
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
            )
            .execute()
        )
        rows = list(response.get("files", []))
        return [
            self._document_match(rows[index], score)
            for index, score in self._order(query, rows)[:limit]
        ]

    @staticmethod
    def _order(query: str, rows: list[dict[str, Any]]) -> list[tuple[int, float | None]]:
        """Rank by title, then keep the rest in Drive's recency order.

        A file can match on body text the API never shows us, so a title that
        shares nothing with the query is not evidence of irrelevance -- dropping
        those would discard exactly the matches full-text search is for. Ranked
        files lead; the remainder keeps its place with no score rather than an
        invented one.
        """
        titles = [str(row.get("name") or "") for row in rows]
        ranked = rank(query, titles)
        ordered: list[tuple[int, float | None]] = [
            (index, round(score, 4)) for index, score in ranked
        ]
        seen = {index for index, _ in ranked}
        ordered.extend((index, None) for index in range(len(rows)) if index not in seen)
        return ordered

    @staticmethod
    def _document_match(row: dict[str, Any], score: float | None) -> ContextMatch:
        modified = str(row.get("modifiedTime") or "")
        mime_type = str(row.get("mimeType") or "Google Drive file")
        return ContextMatch(
            source_name="google_docs",
            match_type=MatchType.RELATED_DOCUMENT,
            title=str(row.get("name") or "Untitled document"),
            snippet=f"{mime_type} last modified {modified[:10]}",
            ref=row.get("webViewLink"),
            score=score,
            occurred_on=_date_from_api(modified),
        )

    def _search_tasks(self, subject: str, query: str, limit: int) -> list[ContextMatch]:
        service = self._build_tasks_service(subject)
        task_lists = service.tasklists().list().execute().get("items", [])
        rows: list[dict[str, Any]] = []
        for task_list in task_lists:
            task_list_id = task_list.get("id")
            if not task_list_id:
                continue
            response = (
                service.tasks()
                .list(
                    tasklist=task_list_id,
                    showCompleted=False,
                    showHidden=False,
                    maxResults=TASK_CANDIDATES,
                )
                .execute()
            )
            rows.extend(response.get("items", []))

        searchable = [f"{row.get('title') or ''}\n{row.get('notes') or ''}" for row in rows]
        matches: list[ContextMatch] = []
        for index, score in rank(query, searchable)[:limit]:
            row = rows[index]
            title = str(row.get("title") or "Untitled task")
            matches.append(
                ContextMatch(
                    source_name="google_tasks",
                    match_type=MatchType.OPEN_TASK,
                    title=title,
                    snippet=str(row.get("notes") or title),
                    ref=row.get("selfLink") or row.get("id"),
                    score=round(score, 4),
                    occurred_on=_date_from_api(row.get("due") or row.get("updated")),
                )
            )
        return matches
