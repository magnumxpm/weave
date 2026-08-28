from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from weave_common import ContextMatch, MatchType

from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal
from agent.context_sources.registry import build_sources, register_source, search_all
from agent.context_sources.sources.meeting_summary_source import MeetingSummarySource
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


def test_config_builds_all_google_sources_in_declared_order() -> None:
    sources = build_sources(
        {
            "sources": [
                {"name": "prior_meetings"},
                {"name": "google_docs"},
                {"name": "google_tasks"},
            ]
        },
        source_kwargs={
            "prior_meetings": {"client": FakeFirestoreClient([])},
            "google_docs": {"base_url": "", "audience": ""},
            "google_tasks": {"base_url": "", "audience": ""},
        },
    )
    assert [source.name for source in sources] == [
        "prior_meetings",
        "google_docs",
        "google_tasks",
    ]


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
    source = PriorMeetingSource(
        client=client,
        embed_query_fn=lambda query: (_ for _ in ()).throw(RuntimeError(query)),
    )

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
    source = PriorMeetingSource(
        client=client,
        embed_query_fn=lambda query: (_ for _ in ()).throw(RuntimeError(query)),
    )

    principal = SearchPrincipal(email="owner@example.com")
    assert source.search("renew the parking permit", principal) == []


def test_meeting_summary_source_applies_attendee_acl_before_ranking() -> None:
    documents = [
        FakeSnapshot(
            "visible",
            {
                "conference_record_id": "conferenceRecords/visible",
                "overview": "The team diagnosed an OAuth redirect failure.",
                "topics": ["Authentication"],
                "visible_to": ["owner@example.com"],
                "meeting_date": "2026-08-20",
            },
        ),
        FakeSnapshot(
            "hidden",
            {
                "conference_record_id": "conferenceRecords/hidden",
                "overview": "Secret OAuth acquisition discussion.",
                "visible_to": ["other@example.com"],
                "meeting_date": "2026-08-21",
            },
        ),
    ]

    class Query:
        def __init__(self) -> None:
            self.principal = ""
            self.cap = 40

        def where(self, *, filter: object) -> Query:
            assert filter.field_path == "visible_to"
            assert filter.op_string == "array_contains"
            self.principal = filter.value
            return self

        def order_by(self, field: str, *, direction: object) -> Query:
            del direction
            assert field == "meeting_date"
            return self

        def limit(self, value: int) -> Query:
            self.cap = value
            return self

        def stream(self) -> list[FakeSnapshot]:
            return [row for row in documents if self.principal in row.data["visible_to"]][
                : self.cap
            ]

    class Client:
        def collection(self, name: str) -> Query:
            assert name == "meeting_summaries"
            return Query()

    source = MeetingSummarySource(
        client=Client(),
        embed_query_fn=lambda query: (_ for _ in ()).throw(RuntimeError(query)),
    )
    results = source.search("OAuth failure", SearchPrincipal(email="owner@example.com"))

    assert [result.ref for result in results] == ["meeting_summaries/visible"]
    assert results[0].match_type is MatchType.MEETING_SUMMARY
    assert results[0].conference_record_id == "conferenceRecords/visible"
    assert all("Secret" not in result.snippet for result in results)


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
                    ("meeting_summaries/abc123", "this meeting summary"),
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


def test_vector_search_keeps_the_acl_prefilter_and_maps_results() -> None:
    calls: list[tuple[str, object]] = []

    class VectorQuery:
        def __init__(self) -> None:
            self.filtered = False

        def where(self, *, filter: object) -> VectorQuery:
            assert filter.field_path == "visible_to"
            assert filter.op_string == "array_contains"
            assert filter.value == "owner@example.com"
            self.filtered = True
            calls.append(("where", filter))
            return self

        def find_nearest(self, **kwargs: object) -> VectorQuery:
            assert self.filtered, "find_nearest must never run without the ACL prefilter"
            assert kwargs["limit"] == 7
            calls.append(("find_nearest", kwargs))
            return self

        def stream(self) -> list[FakeSnapshot]:
            return [
                FakeSnapshot(
                    "prior",
                    {
                        "description": "Expense claim still needs finance approval",
                        "title": "Submit the expense claim",
                        "meeting_date": "2026-08-01",
                        "vector_distance": 0.12,
                    },
                )
            ]

    class Client:
        def collection(self, name: str) -> VectorQuery:
            assert name == "action_items"
            return VectorQuery()

    source = PriorMeetingSource(client=Client(), embed_query_fn=lambda query: [0.1] * 768)
    results = source.search(
        "reimbursement paperwork", SearchPrincipal(email="owner@example.com"), limit=7
    )

    assert [call[0] for call in calls] == ["where", "find_nearest"]
    assert results[0].score == pytest.approx(0.88)
    assert results[0].occurred_on == date(2026, 8, 1)
    assert results[0].title == "Submit the expense claim"


def test_vector_failure_falls_back_to_lexical_results() -> None:
    client = FakeFirestoreClient(
        [
            FakeSnapshot(
                "prior",
                {
                    "description": "prepare launch readiness report",
                    "visible_to": ["owner@example.com"],
                    "created_at": datetime(2026, 8, 1, tzinfo=UTC),
                },
            )
        ]
    )
    source = PriorMeetingSource(client=client, embed_query_fn=lambda query: [0.1] * 768)

    results = source.search("launch report", SearchPrincipal(email="owner@example.com"))

    assert [result.ref for result in results] == ["prior"]


def test_empty_query_never_calls_the_embedder() -> None:
    calls: list[str] = []
    source = PriorMeetingSource(
        client=FakeFirestoreClient([]), embed_query_fn=lambda query: calls.append(query) or []
    )
    assert source.search("   ", SearchPrincipal(email="owner@example.com")) == []
    assert calls == []


def test_context_tool_explicitly_requests_wide_recall() -> None:
    class RecallSource(ContextSource):
        name = "recall"
        auth_mode = AuthMode.USER_CONTEXT

        def search(
            self, query: str, principal: SearchPrincipal, limit: int = 5
        ) -> list[ContextMatch]:
            del query, principal
            return [
                ContextMatch(
                    source_name=self.name,
                    match_type=MatchType.EXISTING_PRIOR_ITEM,
                    title=f"candidate {index}",
                    snippet="candidate",
                )
                for index in range(limit)
            ]

    tool = make_search_related_context_tool([RecallSource()])
    results = tool(
        "query",
        SimpleNamespace(state={"search_principal": SearchPrincipal(email="owner@example.com")}),
    )
    assert len(results) == 20


def test_an_empty_query_embedding_falls_back_instead_of_reporting_no_context() -> None:
    # An embedder returning nothing has failed quietly; treating that as a
    # successful empty search would hide the outage behind "no related context".
    client = FakeFirestoreClient(
        [
            FakeSnapshot(
                "prior",
                {
                    "description": "renew the parking permit for the team",
                    "visible_to": ["owner@example.com"],
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )
        ]
    )
    source = PriorMeetingSource(client=client, embed_query_fn=lambda query: [])

    results = source.search("parking permit renewal", SearchPrincipal(email="owner@example.com"))

    assert [result.ref for result in results] == ["prior"]
