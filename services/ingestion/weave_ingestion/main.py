"""Synchronous Pub/Sub push handler: the entire pipeline runs inside one request.

Flow (build plan v2): verify OIDC → claim meeting (idempotency lease) → fetch
artifacts → Model Armor input → Agent Engine pipeline → isolate delivery per
owner → write action items → mark delivered. Retryable processing exceptions
mark `failed`; owner delivery exceptions are recorded and acknowledged.
"""

from __future__ import annotations

import base64
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from weave_common import PipelineRequest, PipelineResult

from weave_ingestion.agent_client import AgentEngineClient
from weave_ingestion.chat_events import ChatClickEvent, parse_chat_event
from weave_ingestion.commitments import CommitmentStore, make_llm_decider, reconcile_meeting
from weave_ingestion.config import Settings, settings_from_env
from weave_ingestion.copilot_client import CopilotEngineClient
from weave_ingestion.delivery.base import Deliverer, MeetingHeader
from weave_ingestion.delivery.chat import ChatDeliverer
from weave_ingestion.delivery.log import LogDeliverer
from weave_ingestion.firestore_client import MeetingLedger, OnboardedUser, WrittenActionItem
from weave_ingestion.google_sources import (
    DRIVE_SCOPE,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    TASKS_SCOPE,
    GoogleSourceBroker,
)
from weave_ingestion.logging_config import configure_logging
from weave_ingestion.meet_client import (
    FixtureMeetArtifactSource,
    MeetArtifactSource,
    extract_conference_id,
    extract_subscriber_user_id,
)
from weave_ingestion.model_armor import TranscriptScreen
from weave_ingestion.oidc import PushAuthError, verify_caller_token, verify_push_token

logger = logging.getLogger(__name__)

TokenVerifier = Callable[[str], dict[str, Any]]
RunPipeline = Callable[[PipelineRequest], PipelineResult]
TriggerSweep = Callable[[], None]
WelcomeSender = Callable[[OnboardedUser], None]
CopilotAsk = Callable[[str, str, str], Awaitable[str] | str]
ChatMessageSender = Callable[[str, dict[str, Any], str | None], None]
ReconcileRows = Callable[[Iterable[WrittenActionItem]], Any]


DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"


class ContextSearchRequest(BaseModel):
    """Strict, intentionally small request accepted by the context broker."""

    model_config = ConfigDict(extra="forbid")

    source: str
    query: str
    principal_email: str
    limit: int = Field(default=5)


def _ingestion_sa(settings: Settings) -> str:
    return f"weave-ingestion-sa@{settings.project_id}.iam.gserviceaccount.com"


def _agent_sa(settings: Settings) -> str:
    return f"weave-agent-sa@{settings.project_id}.iam.gserviceaccount.com"


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

    def build_drive_service(subject: str):
        credentials = delegated_credentials(subject, [DRIVE_SCOPE], _ingestion_sa(settings))
        return build("drive", "v3", credentials=credentials)

    return LiveMeetArtifactSource(
        build_meet_service,
        directory.email_for_user_id,
        build_drive_service,
    )


def _build_google_source_broker(settings: Settings) -> GoogleSourceBroker:
    from googleapiclient.discovery import build

    from weave_ingestion.google_auth import delegated_credentials

    def build_drive_service(subject: str):
        credentials = delegated_credentials(subject, [DRIVE_SCOPE], _ingestion_sa(settings))
        return build("drive", "v3", credentials=credentials)

    def build_tasks_service(subject: str):
        credentials = delegated_credentials(subject, [TASKS_SCOPE], _ingestion_sa(settings))
        return build("tasks", "v1", credentials=credentials)

    return GoogleSourceBroker(build_drive_service, build_tasks_service)


def _build_chat_client():
    from google.auth import default as default_credentials
    from googleapiclient.discovery import build

    credentials, _ = default_credentials(scopes=["https://www.googleapis.com/auth/chat.bot"])
    return build("chat", "v1", credentials=credentials)


def _build_chat_deliverer(directory) -> Deliverer:
    client = _build_chat_client()
    # App-authenticated Chat calls cannot use an email alias, so resolve the
    # owner's numeric id through the directory.
    return ChatDeliverer(client, lambda email: f"users/{directory.user_id_for_email(email)}")


def _build_sweep_trigger(settings: Settings) -> TriggerSweep:
    def trigger() -> None:
        if not settings.subscription_job_name:
            raise RuntimeError("SUBSCRIPTION_JOB_NAME is required for Chat onboarding")
        from google.cloud import run_v2

        # Starting the job returns a long-running operation. Deliberately do
        # not call result(): onboarding only waits for execution submission.
        run_v2.JobsClient().run_job(name=settings.subscription_job_name)

    return trigger


def _build_welcome_sender() -> WelcomeSender:
    client = None

    def send(user: OnboardedUser) -> None:
        nonlocal client
        if not user.dm_space:
            return
        client = client or _build_chat_client()
        request_id = str(uuid5(NAMESPACE_URL, f"weave:{user.user_id}:{user.dm_space}"))
        body = {
            "cardsV2": [
                {
                    "cardId": "weave-welcome",
                    "card": {
                        "header": {"title": "Weave is ready"},
                        "sections": [
                            {
                                "widgets": [
                                    {
                                        "textParagraph": {
                                            "text": (
                                                "Your Meet action items will appear here after "
                                                "transcription is complete.<br><br>Ask me anything "
                                                "about your commitments, e.g. <i>what needs my "
                                                "attention this week?</i>"
                                            )
                                        }
                                    }
                                ]
                            }
                        ],
                    },
                }
            ]
        }
        (
            client.spaces()
            .messages()
            .create(
                parent=user.dm_space,
                body=body,
                messageId="client-weave-welcome-v1",
                requestId=request_id,
            )
            .execute()
        )

    return send


def _build_chat_message_sender() -> ChatMessageSender:
    client = None

    def send(space_name: str, body: dict[str, Any], message_name: str | None = None) -> None:
        del message_name  # DMs do not require a thread; keep the seam for future spaces.
        nonlocal client
        client = client or _build_chat_client()
        client.spaces().messages().create(parent=space_name, body=body).execute()

    return send


def _copilot_body(answer: Any) -> dict[str, Any]:
    """Draw a commitment listing as a card; leave ordinary answers as text.

    The prose rides along above the card rather than being replaced by it, so a
    Chat reader and a Gemini Enterprise reader are told the same thing -- the
    card is a nicer rendering of that answer, not a different answer.
    """
    from weave_ingestion.copilot_client import CopilotAnswer
    from weave_ingestion.delivery.chat_text import to_chat_text
    from weave_ingestion.delivery.commitment_card import build_card_from_rows

    if not isinstance(answer, CopilotAnswer):
        return {"text": to_chat_text(str(answer))}
    body: dict[str, Any] = {"text": to_chat_text(answer.text)}
    if rows := answer.commitment_rows():
        body["cardsV2"] = [build_card_from_rows(rows, today=datetime.now(UTC).date())]
    return body


def _build_reconciler(store: CommitmentStore) -> ReconcileRows:
    decider = None

    def reconcile(rows: Iterable[WrittenActionItem]) -> Any:
        nonlocal decider
        rows = list(rows)
        if not rows:
            return []
        decider = decider or make_llm_decider()
        return reconcile_meeting(store, decider, rows)

    return reconcile


def _meeting_header(request: PipelineRequest, owner_email: str) -> MeetingHeader:
    owner = owner_email.strip().casefold()
    participant_names = tuple(
        attendee.display_name
        for attendee in request.attendees
        if attendee.email.strip().casefold() != owner
    )
    return MeetingHeader(
        title=request.meeting_title,
        started_at=request.started_at,
        participant_names=participant_names,
    )


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
    trigger_sweep: TriggerSweep | None = None,
    welcome_sender: WelcomeSender | None = None,
    broker: GoogleSourceBroker | None = None,
    caller_token_verifier: TokenVerifier | None = None,
    commitment_store: CommitmentStore | None = None,
    reconcile_rows: ReconcileRows | None = None,
    copilot_client: CopilotAsk | None = None,
    chat_message_sender: ChatMessageSender | None = None,
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
    owns_ledger = ledger is None
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

    trigger_sweep = trigger_sweep or _build_sweep_trigger(settings)
    welcome_sender = welcome_sender or _build_welcome_sender()
    chat_message_sender = chat_message_sender or _build_chat_message_sender()
    commitment_store = commitment_store or CommitmentStore()
    if reconcile_rows is None:
        # Injected ledgers are generally hermetic tests or local demos. They opt
        # into reconciliation explicitly; the production ledger always gets it.
        reconcile_rows = _build_reconciler(commitment_store) if owns_ledger else lambda rows: []
    if copilot_client is None and settings.copilot_engine_id:
        copilot_client = CopilotEngineClient(
            settings.copilot_engine_id, settings.project_id, settings.region
        ).ask
    broker = broker or _build_google_source_broker(settings)
    if caller_token_verifier is None:

        def caller_token_verifier(token: str) -> dict[str, Any]:
            return verify_caller_token(
                token,
                audience=settings.pubsub_push_audience,
                expected_sa=_agent_sa(settings),
            )

    app = FastAPI()

    def authorize(request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return False
        try:
            token_verifier(token)
        except PushAuthError as error:
            logger.warning("rejected push", extra={"reason": str(error)})
            return False
        return True

    def authorize_broker(request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return False
        try:
            caller_token_verifier(token)
        except PushAuthError as error:
            logger.warning("rejected context broker caller", extra={"reason": str(error)})
            return False
        return True

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/context/search")
    async def context_search(request: Request) -> Response:
        if not authorize_broker(request):
            return Response(status_code=403)
        try:
            payload = ContextSearchRequest.model_validate(await request.json())
        except (ValidationError, ValueError, TypeError):
            return Response(status_code=400)

        subject = payload.principal_email.strip().casefold()
        if subject not in ledger.onboarded_by_email():
            logger.info(
                "broker refused non-onboarded subject",
                extra={"subject": subject, "source": payload.source},
            )
            return JSONResponse({"matches": []})

        limit = max(1, min(payload.limit, MAX_LIMIT))
        query = payload.query[:MAX_QUERY_CHARS]
        if payload.source not in {"google_docs", "google_tasks"}:
            return Response(status_code=400)
        try:
            matches = broker.search(payload.source, query, subject, limit)
        except Exception as error:  # noqa: BLE001 - isolate broker/API failures from the pipeline
            # Google API errors can include the request URL (and therefore the
            # search text) in their message. Record only the exception type.
            logger.warning(
                "context broker search failed",
                extra={
                    "subject": subject,
                    "source": payload.source,
                    "result_count": 0,
                    "error_type": type(error).__name__,
                },
            )
            return Response(status_code=502)

        logger.info(
            "context broker search completed",
            extra={"subject": subject, "source": payload.source, "result_count": len(matches)},
        )
        return JSONResponse({"matches": [match.model_dump(mode="json") for match in matches]})

    @app.post("/pubsub-push")
    async def pubsub_push(request: Request) -> Response:
        if not authorize(request):
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
            onboarded = ledger.onboarded_by_email()
            delivery_outcomes: dict[str, str] = {}
            for bundle in result.bundles:
                owner = bundle.owner_email
                target = onboarded.get(owner.strip().casefold())
                if target is None:
                    delivery_outcomes[owner] = "skipped_not_onboarded"
                    continue
                try:
                    deliverer.deliver(
                        owner,
                        bundle,
                        target,
                        meeting=_meeting_header(pipeline_request, owner),
                    )
                    delivery_outcomes[owner] = "delivered"
                except Exception:  # noqa: BLE001 - isolate one owner's delivery
                    logger.exception(
                        "delivery failed",
                        extra={"conference_id": conference_id, "owner_email": owner},
                    )
                    delivery_outcomes[owner] = "delivery_failed"
            written_rows = (
                ledger.write_action_items(
                    conference_id,
                    result.bundles,
                    visible_to=[attendee.email for attendee in pipeline_request.attendees],
                )
                or []
            )
            reconcile_status = "completed"
            try:
                reconcile_rows(written_rows)
            except Exception:  # noqa: BLE001 - delivery/history already succeeded
                reconcile_status = "failed"
                logger.exception(
                    "commitment reconciliation failed",
                    extra={"conference_id": conference_id},
                )
            status = (
                "delivered_partial"
                if "delivery_failed" in delivery_outcomes.values()
                else "delivered"
            )
            ledger.mark(
                conference_id,
                status,
                delivery_outcomes,
                reconcile=reconcile_status,
            )
            outcome_counts = Counter(delivery_outcomes.values())
            logger.info(
                "meeting processed",
                extra={
                    "conference_id": conference_id,
                    "owner_count": len(result.bundles),
                    "dropped_item_count": result.dropped_item_count,
                    "delivery_outcomes": dict(outcome_counts),
                },
            )
            return Response(status_code=200)
        except Exception:
            logger.exception("processing failed", extra={"conference_id": conference_id})
            ledger.mark(conference_id, "failed")
            return Response(status_code=500)

    @app.post("/chat-events")
    async def chat_events(request: Request) -> Response:
        if not authorize(request):
            return Response(status_code=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise TypeError("Pub/Sub body must be an object")
            message = body.get("message") or {}
            if not isinstance(message, dict):
                raise TypeError("Pub/Sub message must be an object")
            encoded = message.get("data", "")
            if not isinstance(encoded, str):
                raise TypeError("Pub/Sub data must be base64 text")
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8", errors="strict")
            raw = json.loads(decoded)
            event = parse_chat_event(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning("malformed Chat event acked")
            return Response(status_code=200)
        if event is None:
            # A user messaging the app lands here legitimately, but so does an
            # unrecognised payload shape. Chat's Pub/Sub envelope is only
            # verifiable against a real install, so log enough to tell the two
            # apart rather than acking a failed onboarding into silence.
            envelope = raw if isinstance(raw, dict) else {}
            space = envelope.get("space")
            logger.info(
                "chat event ignored",
                extra={
                    "event_type": envelope.get("type") or envelope.get("eventType"),
                    "payload_keys": sorted(envelope),
                    "space_type": space.get("spaceType") if isinstance(space, dict) else None,
                },
            )
            return Response(status_code=200)

        if isinstance(event, ChatClickEvent):
            if event.function == "mark_done":
                try:
                    if resolve_subject_email is None:
                        raise LookupError("cannot resolve Chat click identity")
                    owner_email = resolve_subject_email(event.user_id).strip().casefold()
                    if not event.conference_id or not event.item_index:
                        raise ValueError("mark_done is missing item identity")
                    item_index = int(event.item_index)
                    if item_index < 1:
                        raise ValueError("item index must start at one")
                    mention_ref = f"{event.conference_id}--{owner_email}--{item_index - 1}"
                    commitment_id = commitment_store.commitment_for_mention(mention_ref)
                    changed = bool(
                        commitment_id
                        and commitment_store.close(
                            commitment_id, owner_email, closed_by="card_click"
                        )
                    )
                    target = ledger.onboarded_by_email().get(owner_email)
                    if changed and target and target.dm_space:
                        chat_message_sender(
                            target.dm_space,
                            {"text": "Done — I marked that commitment complete in Weave."},
                            None,
                        )
                    logger.info(
                        "Chat commitment close processed",
                        extra={"user_id": event.user_id, "updated": changed},
                    )
                except Exception:  # noqa: BLE001 - click redelivery must not loop
                    logger.exception(
                        "Chat commitment close failed", extra={"user_id": event.user_id}
                    )
                return Response(status_code=200)
            logger.info(
                "Chat action-card click acknowledged",
                extra={
                    "function": event.function,
                    "conference_id": event.conference_id,
                    "item_index": event.item_index,
                    "user_id": event.user_id,
                },
            )
            return Response(status_code=200)

        try:
            email = event.email
            if email is None:
                if resolve_subject_email is None:
                    raise LookupError("cannot resolve Chat user email")
                email = resolve_subject_email(event.user_id)

            if event.kind == "added":
                already_onboarded = email.strip().casefold() in ledger.onboarded_by_email()
                user = ledger.upsert_onboarded_user(
                    user_id=event.user_id,
                    email=email or "",
                    dm_space=event.space_name,
                )
                trigger_sweep()
                message = (event.message_text or "").strip()
                if not already_onboarded:
                    try:
                        welcome_sender(user)
                    except Exception:  # noqa: BLE001 - confirmation is best-effort
                        logger.exception(
                            "welcome delivery failed", extra={"user_id": event.user_id}
                        )
                if message and not message.startswith("/") and copilot_client is not None:
                    try:
                        reply = copilot_client(user.email, event.space_name, message)
                        if isawaitable(reply):
                            reply = await reply
                        chat_message_sender(
                            event.space_name, _copilot_body(reply), event.message_name
                        )
                    except Exception:  # noqa: BLE001 - never redeliver a conversational turn
                        logger.exception(
                            "Chat copilot failed", extra={"space_name": event.space_name}
                        )
                        try:
                            chat_message_sender(
                                event.space_name,
                                {
                                    "text": "Sorry, I couldn't check your commitments "
                                    "just now. Please try again."
                                },
                                event.message_name,
                            )
                        except Exception:  # noqa: BLE001 - best-effort fixed failure reply
                            logger.exception(
                                "Chat copilot apology failed",
                                extra={"space_name": event.space_name},
                            )
            else:
                ledger.mark_offboarding(
                    user_id=event.user_id,
                    email=email,
                    dm_space=event.space_name,
                )
                trigger_sweep()

            logger.info(
                "Chat onboarding event processed",
                extra={"kind": event.kind, "user_id": event.user_id},
            )
            return Response(status_code=200)
        except Exception:
            logger.exception(
                "Chat onboarding failed", extra={"kind": event.kind, "user_id": event.user_id}
            )
            return Response(status_code=500)

    return app


def __getattr__(name: str) -> Any:
    if name == "app":  # uvicorn entrypoint; env validation happens right here
        configure_logging()
        return create_app(settings_from_env())
    raise AttributeError(name)
