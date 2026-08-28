"""Pydantic contracts shared by the agent and ingestion runtimes."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommitmentStatus(StrEnum):
    """Resolution of a proposed action; silence is always unresolved."""

    ACCEPTED = "accepted"
    DECLINED = "declined"
    DEFERRED = "deferred"
    REASSIGNED = "reassigned"
    UNRESOLVED = "unresolved"


class CommitmentState(StrEnum):
    """Human-controlled lifecycle state for a derived commitment."""

    OPEN = "open"
    WAITING = "waiting"
    LIKELY_COMPLETE = "likely_complete"
    CLOSED = "closed"


class MentionRelationship(StrEnum):
    """How an immutable meeting mention relates to its commitment."""

    ORIGINAL = "original"
    RESTATED = "restated"
    CARRIED_OVER = "carried_over"
    PROGRESS_EVIDENCE = "progress_evidence"
    COMPLETION_EVIDENCE = "completion_evidence"


ACTIONABLE_STATUSES: frozenset[CommitmentStatus] = frozenset(
    {CommitmentStatus.ACCEPTED, CommitmentStatus.REASSIGNED}
)

IDENTITY_CONFIDENCE_FLOOR = 0.85


class ActionType(StrEnum):
    TASK = "task"
    FOLLOW_UP = "follow_up"
    DECISION_NEEDED = "decision_needed"


class MatchType(StrEnum):
    EXISTING_PRIOR_ITEM = "existing_prior_item"
    MEETING_SUMMARY = "meeting_summary"
    RELATED_DISCUSSION = "related_discussion"
    RELATED_DOCUMENT = "related_document"
    OPEN_TASK = "open_task"
    NONE = "none"


class ReferenceStatus(StrEnum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Attendee(FrozenModel):
    email: str
    participant_id: str
    display_name: str


class TranscriptTurn(FrozenModel):
    turn_index: int = Field(ge=0)
    participant_id: str | None
    speaker_name: str
    text: str


class Reference(FrozenModel):
    """One person-reference in an action item, resolved or explicitly unknown."""

    mention: str
    turn_ref: int = Field(ge=0)
    status: ReferenceStatus
    email: str | None = None
    display_name: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def identity_matches_status(cls, data: Any) -> Any:
        """Coerce a half-identified reference instead of rejecting it.

        A response schema cannot say "email is required only when status is
        resolved", so a model is free to emit either inconsistency. Raising
        here would fail MeetingInsights validation and lose every item of
        the meeting over one pronoun, so the inconsistency is resolved the
        conservative way: an identity that is not complete is no identity.
        `ground_references` still decides which surviving ones are trusted.
        """
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get("status") == ReferenceStatus.RESOLVED and not (
            normalized.get("email") and normalized.get("display_name")
        ):
            normalized["status"] = ReferenceStatus.UNKNOWN
        if normalized.get("status") == ReferenceStatus.UNKNOWN:
            normalized.update(email=None, display_name=None, confidence=0.0)
        return normalized


class ActionItem(FrozenModel):
    description: str
    source_text: str | None = None
    references: list[Reference] = Field(default_factory=list)
    action_type: ActionType
    status: CommitmentStatus
    owner_email: str | None
    owner_confidence: float = Field(ge=0.0, le=1.0)
    commitment_turn_ref: int | None = Field(default=None, ge=0)
    resolution_turn_ref: int | None = Field(default=None, ge=0)
    deadline: date | None = None
    deadline_source_text: str | None = None
    # What the transcript says must happen before this can start. Only ever a
    # stated precondition, never inferred from two items sharing a topic -- a
    # guessed dependency corrupts exactly the "what should I do first" answer
    # the commitment graph exists to give.
    blocked_on: str | None = None

    @model_validator(mode="after")
    def accepted_item_has_resolution(self) -> ActionItem:
        """An accepted commitment must point to the explicit acceptance turn."""
        if self.status is CommitmentStatus.ACCEPTED and self.resolution_turn_ref is None:
            raise ValueError("accepted items require resolution_turn_ref")
        return self

    def is_actionable(self) -> bool:
        return self.status in ACTIONABLE_STATUSES


class MeetingSummaryContent(FrozenModel):
    """Bounded, transcript-grounded context shared across one meeting."""

    overview: str = Field(min_length=1, max_length=2000)
    topics: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        default_factory=list, max_length=12
    )
    decisions: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        default_factory=list, max_length=20
    )
    implementation_notes: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    reproduction_steps: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )


class MeetingInsights(FrozenModel):
    conference_record_id: str
    meeting_date: date
    summary: MeetingSummaryContent
    items: list[ActionItem] = Field(default_factory=list)

    def items_for_owner(self, email: str) -> list[ActionItem]:
        normalized = email.strip().casefold()
        if not normalized:
            return []
        return [
            item
            for item in self.items
            if item.is_actionable()
            and item.owner_email is not None
            and item.owner_email.strip().casefold() == normalized
        ]


class ContextMatch(FrozenModel):
    source_name: str
    match_type: MatchType
    title: str
    snippet: str
    ref: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    occurred_on: date | None = None
    conference_record_id: str | None = None


class CommitmentMention(FrozenModel):
    mention_ref: str
    meeting_date: date
    relationship: MentionRelationship
    excerpt: str
    meeting_summary_ref: str | None = None


class Commitment(FrozenModel):
    commitment_id: str
    owner_email: str
    title: str
    status: CommitmentState
    status_evidence: str | None = None
    status_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    first_seen: date
    last_mentioned: date
    mention_count: int = Field(ge=1)
    deadline: date | None = None
    waiting_on: str | None = None
    first_meeting_summary_ref: str | None = None
    latest_meeting_summary_ref: str | None = None


class ReconcileDecision(FrozenModel):
    """Structured same-commitment judgment for one new mention."""

    matched_commitment_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    relationship: MentionRelationship
    canonical_title: str = Field(max_length=160)
    inferred_state: CommitmentState
    state_evidence: str | None = Field(default=None, max_length=300)
    waiting_on: str | None = Field(default=None, max_length=120)
    blocking_hint: str | None = Field(default=None, max_length=200)


class EnrichedActionItem(FrozenModel):
    item: ActionItem
    matches: list[ContextMatch] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=160)
    details: str | None = Field(default=None, max_length=700)


class OwnerItemList(FrozenModel):
    owner_email: str
    items: list[EnrichedActionItem] = Field(default_factory=list)


class EnrichedOwnerBundle(FrozenModel):
    owner_email: str
    conference_record_id: str
    meeting_date: date
    items: list[EnrichedActionItem] = Field(default_factory=list)
    enriched: bool
    skip_reason: str | None = None

    @model_validator(mode="after")
    def skipped_bundle_has_reason(self) -> EnrichedOwnerBundle:
        if not self.enriched and not self.skip_reason:
            raise ValueError("unenriched bundles require skip_reason")
        if self.enriched and self.skip_reason is not None:
            raise ValueError("enriched bundles cannot have skip_reason")
        return self


class PipelineRequest(FrozenModel):
    transcript_turns: list[TranscriptTurn]
    conference_record_id: str
    meeting_date: date
    attendees: list[Attendee]
    meeting_title: str | None = None
    started_at: datetime | None = None


class PipelineResult(FrozenModel):
    conference_record_id: str
    summary: MeetingSummaryContent
    bundles: list[EnrichedOwnerBundle]
    dropped_item_count: int = Field(ge=0)
