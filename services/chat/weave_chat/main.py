"""Google Chat's interaction endpoint: fast, synchronous, and delegation-free.

Chat only lets an app answer a button click over HTTPS -- a Pub/Sub-connected app
has no channel to reply on, which is why clicks previously died with "Weave is
unable to process your request". So Chat now talks here.

This service stays deliberately small. It is publicly reachable (Chat cannot
present an IAM identity), so it holds no domain-wide delegation and touches only
Firestore and one Pub/Sub topic. Clicks are answered inline because they are two
reads and a write; everything slower -- onboarding, the copilot -- is republished
to the existing `chat-events` topic and handled by the ingestion service exactly
as before. Chat's interaction deadline is far shorter than a copilot turn, so
that split is the whole design.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from weave_common import build_views
from weave_ingestion.chat_events import ChatClickEvent, parse_chat_event
from weave_ingestion.commitments import CommitmentStore
from weave_ingestion.delivery.commitment_card import build_commitment_card
from weave_ingestion.firestore_client import ONBOARDED
from weave_ingestion.logging_config import configure_logging
from weave_ingestion.oidc import PushAuthError

from weave_chat.config import ChatSettings, settings_from_env
from weave_chat.jwt_auth import allowed_senders, describe_token, verify_chat_token
from weave_chat.responses import dialect_of, is_addon_envelope, new_message, update_message

logger = logging.getLogger(__name__)

TokenVerifier = Callable[[str], dict[str, Any]]
Publisher = Callable[[bytes], None]

CLOSE_FUNCTIONS = {"close_commitment", "mark_done"}
REOPEN_FUNCTIONS = {"reopen_commitment"}
# Kept working on purpose: cards already sitting in users' DM history carry these,
# and after the console cutover their clicks arrive here too. An unhandled click
# is exactly what renders the red error, so none may go unanswered.
LEGACY_ACK = {
    "accept_item": "Noted — that stays on your list.",
    "decline_item": "Noted — I won't chase that one.",
}


def _build_publisher(settings: ChatSettings) -> Publisher:
    from google.cloud import pubsub_v1

    client = pubsub_v1.PublisherClient()

    def publish(payload: bytes) -> None:
        client.publish(settings.chat_events_topic, payload).result(timeout=10)

    return publish


class OnboardedReader:
    """Resolve a clicker's identity from storage, never from the request body."""

    def __init__(self, client: Any | None = None, project_id: str | None = None) -> None:
        self._client = client
        self._project_id = project_id

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client(project=self._project_id)
        return self._client

    def email_for(self, user_id: str) -> str | None:
        snapshot = self.client.collection(ONBOARDED).document(user_id).get()
        data = snapshot.to_dict() if getattr(snapshot, "exists", True) else None
        email = (data or {}).get("email")
        return email.strip().casefold() if isinstance(email, str) and email else None


def _payload_shape(value: Any, depth: int = 0) -> Any:
    """Key structure of a payload, three levels deep, with no leaf values.

    The envelope Chat sends over an HTTP endpoint is not documented against the
    Pub/Sub shape this codebase grew up on, and a click that fails to parse is
    silently republished as if it were a message. Logging the shape -- never the
    content -- lets one real interaction document itself.
    """
    if isinstance(value, dict):
        if depth >= 3:
            return sorted(value)
        return {key: _payload_shape(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_payload_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def create_app(
    settings: ChatSettings,
    *,
    token_verifier: TokenVerifier | None = None,
    store: CommitmentStore | None = None,
    onboarded: OnboardedReader | None = None,
    publisher: Publisher | None = None,
) -> FastAPI:
    """Wire the endpoint; every collaborator is injectable for hermetic tests."""
    if token_verifier is None:
        senders = allowed_senders(settings.project_number)

        def token_verifier(token: str) -> dict[str, Any]:
            return verify_chat_token(token, audience=settings.chat_audience, senders=senders)

    store = store or CommitmentStore()
    onboarded = onboarded or OnboardedReader(project_id=settings.project_id)
    publisher = publisher or _build_publisher(settings)

    app = FastAPI()

    def authorize(request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return False
        try:
            token_verifier(token)
        except PushAuthError as error:
            # Log the token's own (unverified) claims alongside the rejection:
            # a wrong audience and an unexpected signer both surface as 401, and
            # only the claims say which one happened.
            logger.warning(
                "rejected Chat caller",
                extra={
                    "reason": str(error),
                    "expected_audience": settings.chat_audience,
                    "token_claims": describe_token(token),
                },
            )
            return False
        return True

    def _rerender(owner_email: str, rendered_ids: str) -> dict[str, Any] | None:
        """Redraw exactly the commitments the clicked card was showing.

        Reading them back by id keeps the refreshed card a function of stored
        state, and the owner guard means an id belonging to someone else simply
        drops out rather than leaking a title.
        """
        ids = [item for item in (rendered_ids or "").split(",") if item]
        if not ids:
            return None
        rows: list[dict[str, Any]] = []
        for commitment_id in ids:
            snapshot = store.client.collection("commitments").document(commitment_id).get()
            data = snapshot.to_dict() if getattr(snapshot, "exists", True) else None
            if not data or str(data.get("owner_email") or "").strip().casefold() != owner_email:
                continue
            rows.append({key: value for key, value in data.items() if key != "embedding"})
            rows[-1]["commitment_id"] = commitment_id
        if not rows:
            return None
        return build_commitment_card(
            build_views(rows, today=datetime.now(UTC).date()),
            button_url=settings.chat_audience,
        )

    def _handle_click(event: ChatClickEvent, addon: bool) -> dict[str, Any]:
        owner_email = onboarded.email_for(event.user_id)
        if owner_email is None:
            return new_message(
                "I don't have you onboarded yet — message me first and I'll set you up.",
                addon=addon,
            )

        if event.function in LEGACY_ACK:
            logger.info("legacy card click acknowledged", extra={"function": event.function})
            return new_message(LEGACY_ACK[event.function], addon=addon)

        commitment_id = event.commitment_id
        if commitment_id is None and event.conference_id and event.item_index:
            # Meeting cards predate commitments, so they address an item by
            # position; storage indexes from zero while the card counts from one.
            index = int(event.item_index) - 1
            if index < 0:
                raise ValueError("item index must start at one")
            commitment_id = store.commitment_for_mention(
                f"{event.conference_id}--{owner_email}--{index}"
            )
        if not commitment_id:
            return new_message("I couldn't find that commitment any more.", addon=addon)

        if event.function in REOPEN_FUNCTIONS:
            changed = store.reopen(commitment_id, owner_email)
            fallback = "Reopened." if changed else "That isn't yours to reopen."
        elif event.function in CLOSE_FUNCTIONS:
            changed = store.close(commitment_id, owner_email, closed_by="card_click")
            fallback = "Done — marked complete." if changed else "That isn't yours to close."
        else:
            logger.info("unknown card function acknowledged", extra={"function": event.function})
            return new_message("I don't know that action.", addon=addon)

        logger.info(
            "Chat card action processed",
            extra={"function": event.function, "updated": changed},
        )
        if changed and (card := _rerender(owner_email, event.rendered_ids or "")):
            return update_message(card, addon=addon)
        return new_message(fallback, addon=addon)

    # Not /healthz: Google's edge answers that path itself before a request
    # reaches the container, so a probe there tells you nothing about this app.
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/")
    async def interact(request: Request) -> Response:
        if not authorize(request):
            return Response(status_code=401)
        raw = await request.body()
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("malformed Chat payload acked")
            return JSONResponse({})

        addon = is_addon_envelope(body)
        logger.info(
            "Chat interaction received",
            extra={"envelope_dialect": dialect_of(body), "shape": _payload_shape(body)},
        )
        event = parse_chat_event(body)

        if isinstance(event, ChatClickEvent):
            try:
                return JSONResponse(_handle_click(event, addon))
            except Exception:  # noqa: BLE001 - a click must never answer with silence
                logger.exception("Chat card action failed", extra={"user_id": event.user_id})
                return JSONResponse(new_message("Something went wrong handling that.", addon=addon))

        # Onboarding and copilot turns are too slow for Chat's interaction
        # deadline, so hand the untouched payload to ingestion and answer now.
        try:
            publisher(raw)
        except Exception:  # noqa: BLE001 - a retried message is worse than a lost one
            logger.exception("republish to chat-events failed")
        return JSONResponse({})

    return app


def __getattr__(name: str) -> Any:
    if name == "app":  # uvicorn entrypoint; env validation happens right here
        # Not basicConfig: it formats only the message, so every `extra` field --
        # including the token claims a rejection depends on -- is dropped.
        configure_logging()
        return create_app(settings_from_env())
    raise AttributeError(name)
