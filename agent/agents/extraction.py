"""Transcript-wide extraction agent and its local runner adapter."""

from __future__ import annotations

from functools import lru_cache

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from weave_common import MeetingInsights, PipelineRequest

from agent.config import MODEL_NAME
from agent.prompts.extraction_prompt import EXTRACTION_PROMPT
from agent.tools.deadline_inference_tool import infer_deadline
from agent.tools.speaker_resolution_tool import resolve_speaker


@lru_cache(maxsize=1)
def get_extraction_agent() -> LlmAgent:
    return LlmAgent(
        name="weave_extraction",
        model=MODEL_NAME,
        instruction=EXTRACTION_PROMPT,
        tools=[resolve_speaker, infer_deadline],
        # A raw JSON schema avoids ADK 2.5 coercing ISO dates to `date` before
        # its internal json.dumps call. The runner still validates the final
        # response into MeetingInsights below.
        output_schema=MeetingInsights.model_json_schema(mode="serialization"),
        include_contents="none",
    )


def _final_text(events: list[object]) -> str:
    for event in reversed(events):
        is_final = getattr(event, "is_final_response", None)
        content = getattr(event, "content", None)
        if callable(is_final) and is_final() and content:
            return "".join(part.text or "" for part in content.parts if part.text)
    raise ValueError("agent returned no final response")


def run_extraction(request: PipelineRequest) -> MeetingInsights:
    runner = InMemoryRunner(agent=get_extraction_agent(), app_name="weave_extraction")
    session = runner.session_service.create_session_sync(
        app_name=runner.app_name,
        user_id="extraction",
        state={
            "attendees": [attendee.model_dump(mode="json") for attendee in request.attendees],
        },
    )
    prompt = request.model_dump_json()
    events = list(
        runner.run(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
        )
    )
    return MeetingInsights.model_validate_json(_final_text(events))
