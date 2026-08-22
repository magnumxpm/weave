"""Synchronous Pub/Sub push handler: the entire pipeline runs inside one request.

Flow (build plan v2): verify OIDC → claim meeting (idempotency lease) → fetch
artifacts → Model Armor input → Agent Engine pipeline → deliver per owner →
write action items → mark delivered. Any exception marks `failed` and returns
500 so Pub/Sub retries, then dead-letters after five attempts.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from weave_common import PipelineRequest, PipelineResult

from weave_ingestion.agent_client import AgentEngineClient
from weave_ingestion.config import Settings, settings_from_env
from weave_ingestion.delivery.base import Deliverer
from weave_ingestion.delivery.chat import ChatDeliverer
from weave_ingestion.delivery.log import LogDeliverer
from weave_ingestion.firestore_client import MeetingLedger
from weave_ingestion.logging_config import configure_logging
from weave_ingestion.meet_client import (
    FixtureMeetArtifactSource,
    MeetArtifactSource,
    extract_conference_id,
    extract_subscriber_user_id,
)
from weave_ingestion.model_armor import TranscriptScreen
from weave_ingestion.oidc import PushAuthError, verify_push_token

logger = logging.getLogger(__name__)

TokenVerifier = Callable[[str], dict[str, Any]]
RunPipeline = Callable[[PipelineRequest], PipelineResult]


DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"


def _ingestion_sa(settings: Settings) -> str:
    return f"weave-ingestion-sa@{settings.project_id}.iam.gserviceaccount.com"


def _build_directory(settings: Settings):
    from googleapiclient.discovery import build

    from weave_ingestion.directory_client import DirectoryClient
    from weave_ingestion.google_auth import delegated_credentials

    credentials = delegated_credentials(
        settings.admin_subject, [DIRECTORY_SCOPE], _ingestion_sa(settings)
    )
    return DirectoryClient(build("admin", "directory_v1", credentials=credentials))


def _build_live_source(settings: Settings, directory) -> MeetArtifactSource:
    from googleapiclient.discovery import build

    from weave_ingestion.google_auth import delegated_credentials
    from weave_ingestion.meet_client import LiveMeetArtifactSource

    def build_meet_service(subject: str):
        credentials = delegated_credentials(subject, [MEET_SCOPE], _ingestion_sa(settings))
        return build("meet", "v2", credentials=credentials)

    return LiveMeetArtifactSource(build_meet_service, directory.email_for_user_id)


def _build_chat_deliverer(directory) -> Deliverer:
    from google.auth import default as default_credentials
    from googleapiclient.discovery import build

    credentials, _ = default_credentials(scopes=["https://www.googleapis.com/auth/chat.bot"])
    client = build("chat", "v1", credentials=credentials)
    # App-authenticated Chat calls cannot use an email alias, so resolve the
    # owner's numeric id through the directory.
    return ChatDeliverer(client, lambda email: f"users/{directory.user_id_for_email(email)}")


def create_app(
    settings: Settings,
    *,
    artifact_source: MeetArtifactSource | None = None,
    ledger: MeetingLedger | None = None,
    deliverer: Deliverer | None = None,
    screen: TranscriptScreen | None = None,
    run_pipeline: RunPipeline | None = None,
    token_verifier: TokenVerifier | None = None,
    resolve_subject_email: Callable[[str], str] | None = None,
) -> FastAPI:
    """Wire the handler; every collaborator is injectable for hermetic tests."""
    directory = None
    if artifact_source is None or deliverer is None:
        needs_directory = settings.artifact_source == "live" or settings.delivery_mode == "chat"
        directory = _build_directory(settings) if needs_directory else None

    if artifact_source is None:
        artifact_source = (
            FixtureMeetArtifactSource(settings.fixture_dir)
            if settings.artifact_source == "fixture"
            else _build_live_source(settings, directory)
        )
    if resolve_subject_email is None and directory is not None:
        resolve_subject_email = directory.email_for_user_id
    if ledger is None:
        ledger = MeetingLedger()
    if deliverer is None:
        deliverer = (
            LogDeliverer() if settings.delivery_mode == "log" else _build_chat_deliverer(directory)
        )
    if screen is None:
        screen = TranscriptScreen(settings.model_armor_input_template, settings.region)
    if run_pipeline is None:
        run_pipeline = AgentEngineClient(
            settings.agent_engine_id, settings.project_id, settings.region
        ).run_pipeline
    if token_verifier is None:

        def token_verifier(token: str) -> dict[str, Any]:
            return verify_push_token(
                token,
                audience=settings.pubsub_push_audience,
                expected_sa=settings.pubsub_push_sa,
            )

    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/pubsub-push")
    async def pubsub_push(request: Request) -> Response:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return Response(status_code=403)
        try:
            token_verifier(token)
        except PushAuthError as error:
            logger.warning("rejected push", extra={"reason": str(error)})
            return Response(status_code=403)

        body = await request.json()
        message = body.get("message", {})
        attributes = message.get("attributes", {}) or {}
        decoded = base64.b64decode(message.get("data", "")).decode("utf-8", errors="replace")
        payload = decoded + json.dumps(attributes)
        conference_id = extract_conference_id(payload)
        if conference_id is None:
            logger.warning("event without conference record id acked")
            return Response(status_code=200)

        if not ledger.claim_meeting(conference_id):
            return Response(status_code=200)

        try:
            # Impersonate the user whose subscription fired: conference records
            # are visible only to that conference's participants.
            subject = None
            if settings.artifact_source == "live":
                subscriber_id = extract_subscriber_user_id(attributes, decoded)
                if subscriber_id is None:
                    # Never guess an identity; surface the shape instead.
                    logger.error(
                        "no subscriber id in event",
                        extra={
                            "conference_id": conference_id,
                            "attribute_keys": sorted(attributes),
                            "attributes": json.dumps(attributes)[:1000],
                        },
                    )
                    raise LookupError("cannot determine which user to read as")
                subject = resolve_subject_email(subscriber_id)
                logger.info(
                    "reading meet artifacts as subscriber",
                    extra={"conference_id": conference_id, "subject": subject},
                )

            pipeline_request = artifact_source.fetch(conference_id, subject)
            transcript_text = "\n".join(turn.text for turn in pipeline_request.transcript_turns)
            if screen.is_blocked(transcript_text):
                ledger.mark(conference_id, "blocked")
                return Response(status_code=200)

            result = run_pipeline(pipeline_request)
            for bundle in result.bundles:
                deliverer.deliver(bundle.owner_email, bundle)
            ledger.write_action_items(
                conference_id,
                result.bundles,
                visible_to=[attendee.email for attendee in pipeline_request.attendees],
            )
            ledger.mark(conference_id, "delivered")
            logger.info(
                "meeting processed",
                extra={
                    "conference_id": conference_id,
                    "owner_count": len(result.bundles),
                    "dropped_item_count": result.dropped_item_count,
                },
            )
            return Response(status_code=200)
        except Exception:
            logger.exception("processing failed", extra={"conference_id": conference_id})
            ledger.mark(conference_id, "failed")
            return Response(status_code=500)

    return app


def __getattr__(name: str) -> Any:
    if name == "app":  # uvicorn entrypoint; env validation happens right here
        configure_logging()
        return create_app(settings_from_env())
    raise AttributeError(name)
