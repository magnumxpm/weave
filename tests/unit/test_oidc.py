from __future__ import annotations

import pytest
from google.oauth2 import id_token
from weave_ingestion.oidc import PushAuthError, verify_caller_token


def test_context_caller_verifier_rejects_a_different_service_account(monkeypatch) -> None:
    monkeypatch.setattr(
        id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "email_verified": True,
            "email": "weave-pubsub-push-sa@test-project.iam.gserviceaccount.com",
        },
    )

    with pytest.raises(PushAuthError, match="unexpected service account"):
        verify_caller_token(
            "token",
            audience="weave-ingestion",
            expected_sa="weave-agent-sa@test-project.iam.gserviceaccount.com",
        )
