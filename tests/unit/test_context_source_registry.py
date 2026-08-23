from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from weave_common import ContextMatch, MatchType

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.registry import build_sources, register_source, search_all
from agent.context_sources.sources.prior_meeting_source import PriorMeetingSource
from agent.tools.search_related_context_tool import make_search_related_context_tool
from tests.unit.fakes import FakeFirestoreClient, FakeSnapshot


@register_source("test_service_only", AuthMode.SERVICE_ONLY)
class MarkerServiceSource(ContextSource):
    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        del query, principal, limit
        return [
            ContextMatch(
                source_name=self.name,
                match_type=MatchType.RELATED_DISCUSSION,
                title="SERVICE MARKER",
                snippet="must not escape",
            )
        ]


def test_service_only_results_never_reach_the_caller() -> None:
    sources = build_sources({"sources": [{"name": "test_service_only"}]})
    principal = SearchPrincipal(email="owner@example.com")
    assert search_all(sources, "query", principal) == []

    tool = make_search_related_context_tool(sources)
    assert tool("query", SimpleNamespace(state={"search_principal": principal})) == []


def test_unknown_source_fails_at_build_time() -> None:
    with pytest.raises(ValueError, match="unknown context source"):
        build_sources({"sources": [{"name": "missing"}]})


def test_failing_source_is_skipped() -> None:
    class FailingSource(ContextSource):
        name = "failing"
        auth_mode = AuthMode.USER_CONTEXT

        def search(
            self, query: str, principal: SearchPrincipal, limit: int = 5
        ) -> list[ContextMatch]:
            raise RuntimeError("boom")

    class WorkingSource(ContextSource):
        name = "working"
        auth_mode = AuthMode.USER_CONTEXT

        def search(
            self, query: str, principal: SearchPrincipal, limit: int = 5
        ) -> list[ContextMatch]:
            return [
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.RELATED_DISCUSSION,
                    title="safe",
                    snippet="safe",
                )
            ]

    results = search_all(
        [FailingSource(), WorkingSource()], "query", SearchPrincipal(email="owner@example.com")
    )
    assert [result.title for result in results] == ["safe"]


def test_tool_without_principal_returns_empty() -> None:
    tool = make_search_related_context_tool([MarkerServiceSource()])
    assert tool("query", SimpleNamespace(state={})) == []


def test_prior_meeting_source_filters_in_query_and_sorts_newest_first() -> None:
    client = FakeFirestoreClient(
        [
            FakeSnapshot(
                "old",
                {
                    "description": "old visible",
                    "visible_to": ["owner@example.com"],
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            ),
            FakeSnapshot(
                "hidden",
                {
                    "description": "secret",
                    "visible_to": ["other@example.com"],
                    "created_at": datetime(2026, 3, 1, tzinfo=UTC),
                },
            ),
            FakeSnapshot(
                "new",
                {
                    "description": "new visible",
                    "visible_to": ["owner@example.com"],
                    "created_at": datetime(2026, 2, 1, tzinfo=UTC),
                },
            ),
        ]
    )
    source = PriorMeetingSource(client=client)

    results = source.search("visible items", SearchPrincipal(email="owner@example.com"))

    assert client.collection_name == "action_items"
    assert [result.ref for result in results] == ["new", "old"]
    assert all("secret" not in result.snippet for result in results)
    assert all(result.score and result.score > 0 for result in results)


def test_prior_meeting_source_returns_nothing_when_nothing_relates() -> None:
    client = FakeFirestoreClient(
        [
            FakeSnapshot(
                "old",
                {
                    "description": "send the launch metrics report",
                    "visible_to": ["owner@example.com"],
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
        ]
    )
    source = PriorMeetingSource(client=client)

    principal = SearchPrincipal(email="owner@example.com")
    assert source.search("renew the parking permit", principal) == []


def test_the_current_meeting_is_not_its_own_prior_context() -> None:
    class OwnMeetingSource(ContextSource):
        name = "own_meeting"
        auth_mode = AuthMode.USER_CONTEXT

        def search(
            self, query: str, principal: SearchPrincipal, limit: int = 5
        ) -> list[ContextMatch]:
            del query, principal, limit
            return [
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.EXISTING_PRIOR_ITEM,
                    title=title,
                    snippet=title,
                    ref=ref,
                )
                for ref, title in (
                    ("abc123--owner@example.com--0", "this meeting, replayed"),
                    ("older99--owner@example.com--0", "an actually prior meeting"),
                )
            ]

    tool = make_search_related_context_tool([OwnMeetingSource()])
    state = {
        "search_principal": SearchPrincipal(email="owner@example.com"),
        "conference_record_id": "conferenceRecords/abc123",
    }

    results = tool("query", SimpleNamespace(state=state))

    assert [result["ref"] for result in results] == ["older99--owner@example.com--0"]
