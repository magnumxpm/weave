from __future__ import annotations

import pytest
from weave_common import ContextMatch, MatchType

from agent.context_sources import broker_client
from agent.context_sources.base import SearchPrincipal
from agent.context_sources.broker_client import TIMEOUT_SECONDS, fetch_broker_matches
from agent.context_sources.registry import search_all
from agent.context_sources.sources.google_docs_source import GoogleDocsSource
from agent.context_sources.sources.google_tasks_source import GoogleTasksSource


def _match(source: str) -> ContextMatch:
    return ContextMatch(
        source_name=source,
        match_type=(MatchType.RELATED_DOCUMENT if source == "google_docs" else MatchType.OPEN_TASK),
        title="Relevant context",
        snippet="Relevant context",
    )


@pytest.mark.parametrize("source_type", [GoogleDocsSource, GoogleTasksSource])
def test_unconfigured_proxy_returns_empty_without_fetching(source_type: type) -> None:
    calls: list[object] = []
    source = source_type(
        base_url="", audience="audience", fetch_fn=lambda *args: calls.append(args)
    )

    assert source.search("query", SearchPrincipal(email="owner@example.com")) == []
    assert calls == []


@pytest.mark.parametrize(
    ("source_type", "source_name"),
    [(GoogleDocsSource, "google_docs"), (GoogleTasksSource, "google_tasks")],
)
def test_configured_proxy_passes_owner_and_returns_matches(
    source_type: type, source_name: str
) -> None:
    calls: list[tuple[object, ...]] = []
    expected = [_match(source_name)]

    def fetch(*args: object) -> list[ContextMatch]:
        calls.append(args)
        return expected

    source = source_type(base_url="https://broker", audience="aud", fetch_fn=fetch)
    result = source.search("launch report", SearchPrincipal(email="owner@example.com"), limit=9)

    assert calls == [
        ("https://broker", "aud", source_name, "launch report", "owner@example.com", 9)
    ]
    assert result is expected


def test_proxy_failure_propagates_to_registry_containment_layer() -> None:
    def failing(*args: object) -> list[ContextMatch]:
        raise RuntimeError("broker unavailable")

    source = GoogleDocsSource(base_url="https://broker", audience="aud", fetch_fn=failing)
    principal = SearchPrincipal(email="owner@example.com")

    with pytest.raises(RuntimeError, match="broker unavailable"):
        source.search("query", principal)
    assert search_all([source], "query", principal) == []


def test_broker_client_mints_oidc_token_and_validates_matches(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            calls["status_checked"] = True

        def json(self) -> dict[str, object]:
            return {"matches": [_match("google_docs").model_dump(mode="json")]}

    monkeypatch.setattr(
        broker_client.id_token,
        "fetch_id_token",
        lambda request, audience: calls.update(audience=audience) or "signed-token",
    )

    def post(url: str, **kwargs: object) -> Response:
        calls.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(broker_client.requests, "post", post)

    matches = fetch_broker_matches(
        "https://broker/",
        "weave-ingestion",
        "google_docs",
        "launch plan",
        "owner@example.com",
        20,
    )

    assert calls["audience"] == "weave-ingestion"
    assert calls["url"] == "https://broker/context/search"
    assert calls["headers"] == {"Authorization": "Bearer signed-token"}
    assert calls["timeout"] == TIMEOUT_SECONDS
    assert calls["status_checked"] is True
    assert matches == [_match("google_docs")]
