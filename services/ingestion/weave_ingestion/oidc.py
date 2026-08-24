"""OIDC verification for Pub/Sub push deliveries."""

from __future__ import annotations

from typing import Any


class PushAuthError(Exception):
    """Any verification failure; the handler answers 403 without detail."""


def verify_push_token(token: str, *, audience: str, expected_sa: str) -> dict[str, Any]:
    """Verify the push subscription's OIDC token against the fixed audience.

    Raises PushAuthError on any mismatch; never returns claims for a token
    minted for another audience or by another service account.
    """
    import google.auth.transport.requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            token, google.auth.transport.requests.Request(), audience=audience
        )
    except Exception as error:
        raise PushAuthError(f"signature/audience check failed: {error}") from error

    if not claims.get("email_verified"):
        raise PushAuthError("email not verified")
    if claims.get("email") != expected_sa:
        raise PushAuthError(f"unexpected service account: {claims.get('email')}")
    return claims


def verify_caller_token(token: str, *, audience: str, expected_sa: str) -> dict[str, Any]:
    """Verify the Agent Engine caller independently of the push identity path."""
    import google.auth.transport.requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            token, google.auth.transport.requests.Request(), audience=audience
        )
    except Exception as error:
        raise PushAuthError(f"signature/audience check failed: {error}") from error

    if not claims.get("email_verified"):
        raise PushAuthError("email not verified")
    if claims.get("email") != expected_sa:
        raise PushAuthError(f"unexpected service account: {claims.get('email')}")
    return claims
