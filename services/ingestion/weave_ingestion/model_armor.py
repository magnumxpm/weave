"""Screen transcript text before any of it reaches a model context."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TranscriptScreen:
    """Model Armor input screening; regional endpoint only, client injectable."""

    def __init__(self, template_name: str, location: str, client: Any | None = None) -> None:
        self._template_name = template_name
        self._location = location
        self._client = client
        self._module: Any = None

    def _ensure_client(self) -> None:
        if self._client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud import modelarmor_v1

            self._module = modelarmor_v1
            self._client = modelarmor_v1.ModelArmorClient(
                client_options=ClientOptions(
                    api_endpoint=f"modelarmor.{self._location}.rep.googleapis.com"
                )
            )
        elif self._module is None:
            from google.cloud import modelarmor_v1

            self._module = modelarmor_v1

    def is_blocked(self, text: str) -> bool:
        """True when the template matches (it enforces only at high confidence)."""
        self._ensure_client()
        response = self._client.sanitize_user_prompt(
            request=self._module.SanitizeUserPromptRequest(
                name=self._template_name,
                user_prompt_data=self._module.DataItem(text=text),
            )
        )
        blocked = (
            response.sanitization_result.filter_match_state
            == self._module.FilterMatchState.MATCH_FOUND
        )
        if blocked:
            logger.warning("model armor blocked transcript input")
        return blocked
