"""Async client for the separately deployed ADK copilot Agent Engine."""

from __future__ import annotations

import hashlib
from typing import Any


def _session_id(space_name: str) -> str:
    # Agent Engine session ids have a conservative character set. The hash is
    # stable without disclosing a Chat resource name in observability surfaces.
    return f"weave-chat-{hashlib.sha256(space_name.encode()).hexdigest()[:32]}"


def _event_text(event: Any) -> str:
    content = event.get("content") if isinstance(event, dict) else None
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))


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

    async def ask(self, principal_email: str, space_name: str, message: str) -> str:
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
        async for event in engine.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=message,
        ):
            text = _event_text(event)
            if text:
                final = text
        if not final.strip():
            raise ValueError("copilot returned no text")
        return final.strip()
