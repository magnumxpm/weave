"""Observability and output-screening callbacks for the agents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class ModelArmorBlockedError(RuntimeError):
    """Raised when Model Armor blocks agent output; degrades that owner to unenriched."""


def make_screen_output_callback(template_name: str, location: str) -> Callable[..., None]:
    """Screen every enrichment model response through a Model Armor template.

    The client is built lazily so importing this module never needs credentials,
    and it must target the regional endpoint (the global one serves nothing).
    """
    state: dict[str, Any] = {}

    def screen_output(callback_context: Any, llm_response: Any) -> None:
        content = getattr(llm_response, "content", None)
        if content is None or not content.parts:
            return None
        text = "".join(part.text or "" for part in content.parts if getattr(part, "text", None))
        if not text.strip():
            return None

        if "client" not in state:
            from google.api_core.client_options import ClientOptions
            from google.cloud import modelarmor_v1

            state["client"] = modelarmor_v1.ModelArmorClient(
                client_options=ClientOptions(
                    api_endpoint=f"modelarmor.{location}.rep.googleapis.com"
                )
            )
            state["module"] = modelarmor_v1

        modelarmor_v1 = state["module"]
        response = state["client"].sanitize_model_response(
            request=modelarmor_v1.SanitizeModelResponseRequest(
                name=template_name,
                model_response_data=modelarmor_v1.DataItem(text=text),
            )
        )
        match_state = response.sanitization_result.filter_match_state
        if match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
            logger.warning("model armor blocked enrichment output")
            raise ModelArmorBlockedError("enrichment output blocked by Model Armor")
        return None

    return screen_output


def log_enrichment_scope(callback_context: Any) -> None:
    state = callback_context.state
    logger.info(
        "starting owner enrichment",
        extra={
            "owner_email": state.get("owner_email"),
            "item_count": len(state.get("owner_items", [])),
            "conference_record_id": state.get("conference_record_id"),
        },
    )
    return None
