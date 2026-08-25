"""Verify that a request really came from Google Chat.

Chat cannot present a Cloud Run IAM identity, so this service is publicly
invocable and the token check *is* the boundary. It mirrors
`weave_ingestion.oidc` deliberately: same lazy imports, same narrow function,
same "raise and let the route answer without detail" contract.

With the app's Authentication Audience set to the HTTP endpoint URL, Chat sends
a Google-signed OIDC token whose `aud` is that URL and whose `email` is Chat's
own service account -- which is exactly the shape `verify_oauth2_token` already
handles for Pub/Sub push and the context broker.
"""

from __future__ import annotations

from typing import Any

from weave_ingestion.oidc import PushAuthError

CHAT_ISSUER = "chat@system.gserviceaccount.com"


def verify_chat_token(token: str, *, audience: str) -> dict[str, Any]:
    """Return Chat's verified claims, or raise; never returns another sender's token."""
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
    if claims.get("email") != CHAT_ISSUER:
        raise PushAuthError(f"unexpected sender: {claims.get('email')}")
    return claims
