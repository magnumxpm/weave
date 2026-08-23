"""Plain-Python orchestration with fresh, owner-isolated enrichment calls."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable

from weave_common import (
    ActionItem,
    EnrichedActionItem,
    EnrichedOwnerBundle,
    MeetingInsights,
    OwnerItemList,
    PipelineRequest,
    PipelineResult,
)

from agent.agents.enrichment import run_enrichment
from agent.agents.extraction import run_extraction
from agent.auth.principal_resolver import PrincipalResolutionError, resolve_principal
from agent.auth.redaction import items_for_enrichment
from agent.auth.reference_grounding import ground_references
from agent.context_sources.base import SearchPrincipal

logger = logging.getLogger(__name__)

Extract = Callable[[PipelineRequest], MeetingInsights]
Enrich = Callable[[SearchPrincipal, list[ActionItem]], OwnerItemList]


def _fingerprint(item: ActionItem) -> str:
    canonical = item.model_dump_json(exclude_none=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def enforce_owner_scope(
    owner_email: str,
    original_items: list[ActionItem],
    result: OwnerItemList,
) -> list[EnrichedActionItem]:
    """Keep only exact input items, with their original multiplicity, for this owner."""
    if result.owner_email.strip().casefold() != owner_email.strip().casefold():
        logger.warning("dropping enrichment with mismatched owner")
        return []

    remaining: dict[str, list[ActionItem]] = {}
    for item in original_items:
        remaining.setdefault(_fingerprint(item), []).append(item)
    accepted: list[EnrichedActionItem] = []
    for enriched_item in result.items:
        fingerprint = _fingerprint(enriched_item.item)
        embedded_owner = enriched_item.item.owner_email
        if (
            embedded_owner is None
            or embedded_owner.strip().casefold() != owner_email.strip().casefold()
            or not remaining.get(fingerprint)
        ):
            logger.warning("dropping out-of-scope enriched item")
            continue
        original = remaining[fingerprint].pop()
        accepted.append(EnrichedActionItem(item=original, matches=enriched_item.matches))
    return accepted


def _unenriched_bundle(
    request: PipelineRequest,
    owner_email: str,
    items: list[ActionItem],
    reason: str,
) -> EnrichedOwnerBundle:
    return EnrichedOwnerBundle(
        owner_email=owner_email,
        conference_record_id=request.conference_record_id,
        meeting_date=request.meeting_date,
        items=[EnrichedActionItem(item=item, matches=[]) for item in items],
        enriched=False,
        skip_reason=reason,
    )


def run_pipeline(
    request: PipelineRequest,
    *,
    extract: Extract = run_extraction,
    enrich: Enrich = run_enrichment,
) -> PipelineResult:
    insights = extract(request)
    grounded_items = [ground_references(item, request.attendees) for item in insights.items]
    grouped: dict[str, list[ActionItem]] = {}
    dropped = 0
    for item in grounded_items:
        if not item.is_actionable() or not item.owner_email:
            dropped += 1
            continue
        owner = item.owner_email.strip().casefold()
        grouped.setdefault(owner, []).append(item)

    attendee_emails = {attendee.email for attendee in request.attendees}
    bundles: list[EnrichedOwnerBundle] = []
    for owner_email, grouped_items in grouped.items():
        owner_items = items_for_enrichment(owner_email, grouped_items)
        try:
            principal = resolve_principal(
                owner_email,
                min(item.owner_confidence for item in owner_items),
                attendee_emails,
            )
        except PrincipalResolutionError as error:
            bundles.append(_unenriched_bundle(request, owner_email, owner_items, error.reason))
            continue

        try:
            result = enrich(principal, owner_items.copy())
            scoped_items = enforce_owner_scope(owner_email, owner_items, result)
            if owner_items and not scoped_items:
                bundles.append(
                    _unenriched_bundle(
                        request,
                        owner_email,
                        owner_items,
                        "enrichment_echo_mismatch",
                    )
                )
                continue
            bundles.append(
                EnrichedOwnerBundle(
                    owner_email=owner_email,
                    conference_record_id=request.conference_record_id,
                    meeting_date=request.meeting_date,
                    items=scoped_items,
                    enriched=True,
                )
            )
        except Exception:  # noqa: BLE001 - one owner's failure must not sink the meeting
            logger.exception("owner enrichment failed", extra={"owner_email": owner_email})
            bundles.append(
                _unenriched_bundle(request, owner_email, owner_items, "enrichment_error")
            )

    return PipelineResult(
        conference_record_id=request.conference_record_id,
        bundles=bundles,
        dropped_item_count=dropped,
    )
