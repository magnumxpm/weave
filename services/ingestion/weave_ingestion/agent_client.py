"""Call the deployed Agent Engine pipeline."""

from __future__ import annotations

from typing import Any

from weave_common import PipelineRequest, PipelineResult

# Must match agent/deployment/deploy.py::QUERY_METHOD; a unit test asserts it.
QUERY_METHOD = "query"


class AgentEngineClient:
    def __init__(self, agent_engine_id: str, project: str, location: str) -> None:
        self._agent_engine_id = agent_engine_id
        self._project = project
        self._location = location
        self._engine: Any = None

    def _ensure_engine(self) -> Any:
        if self._engine is None:
            import vertexai
            from vertexai import agent_engines

            vertexai.init(project=self._project, location=self._location)
            self._engine = agent_engines.get(self._agent_engine_id)
        return self._engine

    def run_pipeline(self, request: PipelineRequest) -> PipelineResult:
        engine = self._ensure_engine()
        response = getattr(engine, QUERY_METHOD)(request=request.model_dump(mode="json"))
        return PipelineResult.model_validate(response)
