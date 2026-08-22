"""Pydantic contracts shared by the agent and ingestion runtimes."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommitmentStatus(StrEnum):
    """Resolution of a proposed action; silence is always unresolved."""

    ACCEPTED = "accepted"
    DECLINED = "declined"
    DEFERRED = "deferred"
    REASSIGNED = "reassigned"
    UNRESOLVED = "unresolved"


ACTIONABLE_STATUSES: frozenset[CommitmentStatus] = frozenset(
    {CommitmentStatus.ACCEPTED, CommitmentStatus.REASSIGNED}
)


class ActionType(StrEnum):
    TASK = "task"
    FOLLOW_UP = "follow_up"
    DECISION_NEEDED = "decision_needed"


class MatchType(StrEnum):
    EXISTING_PRIOR_ITEM = "existing_prior_item"
    RELATED_DISCUSSION = "related_discussion"
    NONE = "none"


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


class ActionItem(FrozenModel):
    description: str
    action_type: ActionType
    status: CommitmentStatus
    owner_email: str | None
    owner_confidence: float = Field(ge=0.0, le=1.0)
    commitment_turn_ref: int | None = Field(default=None, ge=0)
    resolution_turn_ref: int | None = Field(default=None, ge=0)
    deadline: date | None = None
    deadline_source_text: str | None = None

    @model_validator(mode="after")
    def accepted_item_has_resolution(self) -> ActionItem:
        """An accepted commitment must point to the explicit acceptance turn."""
        if self.status is CommitmentStatus.ACCEPTED and self.resolution_turn_ref is None:
            raise ValueError("accepted items require resolution_turn_ref")
        return self

    def is_actionable(self) -> bool:
        return self.status in ACTIONABLE_STATUSES


class MeetingInsights(FrozenModel):
    conference_record_id: str
    meeting_date: date
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


class EnrichedActionItem(FrozenModel):
    item: ActionItem
    matches: list[ContextMatch] = Field(default_factory=list)


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


class PipelineResult(FrozenModel):
    conference_record_id: str
    bundles: list[EnrichedOwnerBundle]
    dropped_item_count: int = Field(ge=0)
