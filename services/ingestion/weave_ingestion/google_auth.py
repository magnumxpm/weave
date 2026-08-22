"""Key-less domain-wide delegation from a Cloud Run service account.

Cloud Run metadata credentials cannot use `with_subject` directly (no private
key), so we sign the DWD assertion via the IAM Credentials API and exchange it
at Google's token endpoint. Requires the SA to hold serviceAccountTokenCreator
on itself (granted in infra/iam.tf).
"""

from __future__ import annotations

import json
import time
from typing import Any

TOKEN_URI = "https://oauth2.googleapis.com/token"


def delegated_credentials(subject: str, scopes: list[str], service_account: str) -> Any:
    import google.auth
    import google.auth.transport.requests
    import google.oauth2.credentials
    from google.auth.transport.requests import AuthorizedSession

    source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(source)

    now = int(time.time())
    assertion = {
        "iss": service_account,
        "sub": subject,
        "aud": TOKEN_URI,
        "iat": now,
        "exp": now + 3600,
        "scope": " ".join(scopes),
    }
    sign_response = session.post(
        "https://iamcredentials.googleapis.com/v1/"
        f"projects/-/serviceAccounts/{service_account}:signJwt",
        json={"payload": json.dumps(assertion)},
    )
    sign_response.raise_for_status()

    token_response = session.post(
        TOKEN_URI,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": sign_response.json()["signedJwt"],
        },
    )
    token_response.raise_for_status()
    return google.oauth2.credentials.Credentials(token=token_response.json()["access_token"])
