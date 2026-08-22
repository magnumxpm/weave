from agent.agents.enrichment import create_enrichment_agent
from agent.agents.extraction import get_extraction_agent
from agent.auth import redaction


def _tool_names(agent: object) -> set[str]:
    names: set[str] = set()
    for tool in agent.tools:
        names.add(getattr(tool, "name", getattr(tool, "__name__", "")))
    return names


def test_extraction_agent_has_no_context_tools() -> None:
    assert _tool_names(get_extraction_agent()) == {"resolve_speaker", "infer_deadline"}


def test_agent_tool_surface_is_read_only() -> None:
    all_tools = _tool_names(get_extraction_agent()) | _tool_names(create_enrichment_agent())
    assert all_tools == {"resolve_speaker", "infer_deadline", "search_related_context"}


def test_adk_output_schemas_keep_dates_json_native() -> None:
    extraction_schema = get_extraction_agent().output_schema
    enrichment_schema = create_enrichment_agent().output_schema
    assert isinstance(extraction_schema, dict)
    assert isinstance(enrichment_schema, dict)
    assert extraction_schema["properties"]["meeting_date"]["type"] == "string"


def test_redaction_imports_the_canonical_actionable_statuses() -> None:
    assert "ACTIONABLE_STATUSES" in redaction.__dict__
    assert redaction.__dict__["ACTIONABLE_STATUSES"].__class__ is frozenset
