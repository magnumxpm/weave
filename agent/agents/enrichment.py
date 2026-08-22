"""Per-owner enrichment agent and isolated runner adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from weave_common import ActionItem, OwnerItemList

from agent.callbacks import log_enrichment_scope
from agent.config import MODEL_NAME
from agent.context_sources.base import ContextSource, SearchPrincipal
from agent.prompts.enrichment_prompt import ENRICHMENT_PROMPT
from agent.tools.search_related_context_tool import (
    make_search_related_context_tool,
    search_related_context,
)


def create_enrichment_agent(sources: Sequence[ContextSource] | None = None) -> LlmAgent:
    tool = (
        search_related_context
        if sources is None
        else make_search_related_context_tool(tuple(sources))
    )
    return LlmAgent(
        name="weave_enrichment",
        model=MODEL_NAME,
        instruction=ENRICHMENT_PROMPT,
        tools=[tool],
        # Keep date values JSON-native inside ADK; the runner performs the
        # authoritative OwnerItemList validation on the final response.
        output_schema=OwnerItemList.model_json_schema(mode="serialization"),
        include_contents="none",
        before_agent_callback=log_enrichment_scope,
    )


def make_run_enrichment(
    sources: Sequence[ContextSource] | None = None,
) -> Callable[[SearchPrincipal, list[ActionItem]], OwnerItemList]:
    agent = create_enrichment_agent(sources)

    def run_enrichment(principal: SearchPrincipal, owner_items: list[ActionItem]) -> OwnerItemList:
        runner = InMemoryRunner(agent=agent, app_name="weave_enrichment")
        session = runner.session_service.create_session_sync(
            app_name=runner.app_name,
            user_id=principal.email,
            state={
                "search_principal": principal.model_dump(mode="json"),
                "owner_email": principal.email,
                "owner_items": [item.model_dump(mode="json") for item in owner_items],
            },
        )
        prompt = OwnerItemList(
            owner_email=principal.email,
            items=[],
        ).model_dump_json()
        serialized_items = ",".join(item.model_dump_json() for item in owner_items)
        prompt += f"\nAction items: [{serialized_items}]"
        events = list(
            runner.run(
                user_id=session.user_id,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
            )
        )
        from agent.agents.extraction import _final_text

        return OwnerItemList.model_validate_json(_final_text(events))

    return run_enrichment


run_enrichment = make_run_enrichment()
