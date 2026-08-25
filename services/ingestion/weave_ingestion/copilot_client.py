"""Async client for the separately deployed ADK copilot Agent Engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# Tools whose payload is a list of commitment rows worth drawing as a card, and
# whether that listing is advice ("what should I do") or an inventory.
COMMITMENT_TOOLS = {
    "suggest_next_actions": True,
    "list_my_commitments": False,
    "find_stale_commitments": False,
}


def _session_id(space_name: str) -> str:
    # Agent Engine session ids have a conservative character set. The hash is
    # stable without disclosing a Chat resource name in observability surfaces.
    return f"weave-chat-{hashlib.sha256(space_name.encode()).hexdigest()[:32]}"


@dataclass(frozen=True)
class ToolResult:
    name: str
    payload: Any


@dataclass(frozen=True)
class CopilotAnswer:
    """The model's prose plus the structured results it drew that prose from."""

    text: str
    tool_results: tuple[ToolResult, ...] = field(default_factory=tuple)

    def commitment_listing(self) -> tuple[list[dict[str, Any]], bool] | None:
        """The last commitment listing this turn, and whether it is advice.

        The listing is what the answer is *about*, so a later tool call (closing
        an item, say) must not change which rows get drawn -- only the most
        recent listing counts, and anything that is not a row list is ignored.
        Returns None when the turn produced no listing at all, which is how an
        ordinary question stays ordinary text.
        """
        for result in reversed(self.tool_results):
            if result.name not in COMMITMENT_TOOLS:
                continue
            payload = result.payload
            if isinstance(payload, list) and all(
                isinstance(row, dict) and row.get("commitment_id") for row in payload
            ):
                return list(payload), COMMITMENT_TOOLS[result.name]
        return None

    def commitment_rows(self) -> list[dict[str, Any]]:
        """Just the rows, for callers that do not care how they were asked for."""
        listing = self.commitment_listing()
        return listing[0] if listing else []


def _parts(event: Any) -> list[dict[str, Any]]:
    content = event.get("content") if isinstance(event, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _tool_result(part: dict[str, Any]) -> ToolResult | None:
    """Read one function-response part, tolerating either key convention."""
    response = part.get("function_response") or part.get("functionResponse")
    if not isinstance(response, dict):
        return None
    name = response.get("name")
    if not isinstance(name, str) or not name:
        return None
    payload = response.get("response")
    # ADK wraps a tool's return value in {"result": ...}; older shapes do not.
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    return ToolResult(name=name, payload=payload)


class CopilotEngineClient:
    def __init__(self, engine_id: str, project: str, location: str) -> None:
        self._engine_id = engine_id
        self._project = project
        self._location = location
        self._engine: Any = None

    def _ensure_engine(self) -> Any:
        if self._engine is None:
            import vertexai
            from vertexai import agent_engines

            vertexai.init(project=self._project, location=self._location)
            self._engine = agent_engines.get(self._engine_id)
        return self._engine

    async def ask(self, principal_email: str, space_name: str, message: str) -> CopilotAnswer:
        engine = self._ensure_engine()
        user_id = principal_email.strip().casefold()
        session_id = _session_id(space_name)
        try:
            await engine.async_get_session(user_id=user_id, session_id=session_id)
        except Exception:  # noqa: BLE001 - create is idempotent with a deterministic id
            try:
                await engine.async_create_session(
                    user_id=user_id,
                    session_id=session_id,
                    state={"copilot_principal": user_id},
                )
            except Exception:  # noqa: BLE001 - another request may have created it
                await engine.async_get_session(user_id=user_id, session_id=session_id)

        final = ""
        results: list[ToolResult] = []
        async for event in engine.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=message,
        ):
            parts = _parts(event)
            text = "".join(str(part.get("text") or "") for part in parts)
            if text.strip():
                final = text
            results.extend(result for part in parts if (result := _tool_result(part)) is not None)
        if not final.strip():
            raise ValueError("copilot returned no text")
        return CopilotAnswer(text=final.strip(), tool_results=tuple(results))
