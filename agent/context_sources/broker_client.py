"""Authenticated client for the ingestion context broker."""

from __future__ import annotations

from typing import Any

import google.auth.transport.requests
import requests
from google.oauth2 import id_token
from weave_common import ContextMatch

TIMEOUT_SECONDS = 8


def fetch_broker_matches(
    base_url: str,
    audience: str,
    source: str,
    query: str,
    principal_email: str,
    limit: int,
) -> list[ContextMatch]:
    """Call the broker with the runtime's OIDC identity and validate its response."""
    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
    response = requests.post(
        f"{base_url.rstrip('/')}/context/search",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "source": source,
            "query": query,
            "principal_email": principal_email,
            "limit": limit,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise ValueError("context broker response must contain a matches list")
    return [ContextMatch.model_validate(match) for match in payload["matches"]]
