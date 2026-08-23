# PLAN — Better reference resolution in action items

The first real card delivered to a user read:

> **Action** — follow up with me regarding the request the support request which I
> raised yesterday for my device because my device has some kind of issue and it's
> probably not working
> **Status** — accepted
> **Commitment turn** — 5
> **Related context** — No related context found

Three defects, in the order the user reported them:

1. **The description is a transcript slice, not an action.** Every deictic
   reference (`me`, `I`, `my`) is left exactly as spoken, so the one fact the
   reader needs — *who* to follow up with — is the fact that got dropped. Gemini's
   own Meet notes resolve these; Weave must too. Where the transcript genuinely
   does not identify a referent (`them`, with no antecedent anywhere), the card
   must say so rather than guess or stay silent.
2. **`Commitment turn` is noise.** A turn index means nothing to the reader.
3. **`Related context` renders even when empty.** "No related context found" is a
   row that only ever costs the reader a line.

This plan covers only those three. It is written for an implementing model:
follow the steps in order, run every ✅ before moving on, and do not start a step
until the previous step's checks pass. `A_PLAN.md`'s engineering ground rules
(Python 3.12, hermetic unit tests, fail closed, full type hints, `make lint &&
make test` green after each step) apply unchanged.

---

## 0. Invariants this feature adds, and which test owns each

Same rule as A_PLAN §0: these are enforced **in Python, never in a prompt**. A
prompt asks the model to do the right thing; these make it impossible for the
model to do the wrong one.

| # | Invariant | Enforced by (code) | Pinned by (test) |
|---|---|---|---|
| 5 | A resolved reference names a **real Meet attendee**: any `Reference` whose email is not in `PipelineRequest.attendees`, or whose confidence is below the identity floor, is demoted to `unknown` — never dropped, never trusted | `ground_references()`, called in `run_pipeline` before grouping | `test_reference_grounding.py` |
| 6 | A resolved reference's `display_name` comes from **Meet attendee data**, never from model output | `ground_references()` overwrites `display_name` from the matched `Attendee` | `test_reference_grounding.py::test_display_name_is_taken_from_meet_not_the_model` |
| 7 | Enrichment can add **matches only**: the delivered `ActionItem` is the extraction object, not the enrichment model's echo of it | `enforce_owner_scope` pairs `matches` with the original item | `test_orchestrator.py::test_enrichment_cannot_alter_the_delivered_item` |
| 8 | An unidentified reference is **visible to the reader**, not silently absent | `build_card` renders an `Unidentified` widget iff an `unknown` reference exists | `test_delivery.py::test_unidentified_mentions_are_shown_only_when_present` |

Invariants 1–4 from A_PLAN are unchanged, and two of them constrain this work:
the extraction agent's tool surface stays `{resolve_speaker, infer_deadline}`
(no new tool), and identity still comes from the Meet API rather than model
prose.

---

## 1. Schema — `shared/weave_common/schemas.py`

Add the reference contract and three optional fields to `ActionItem`.
`deadline_source_text` is the precedent for `source_text`: same idiom, same
purpose (keep the spoken words for provenance, off the card).

```python
IDENTITY_CONFIDENCE_FLOOR = 0.85


class ReferenceStatus(StrEnum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class Reference(FrozenModel):
    """One deictic mention in an action item, resolved or explicitly not."""

    mention: str  # as spoken: "me", "him", "them", "my"
    turn_ref: int = Field(ge=0)  # the turn it was spoken in
    status: ReferenceStatus
    email: str | None = None
    display_name: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def identity_matches_status(cls, data: Any) -> Any:
        # Coerce, never raise: an identity that is not complete is no identity.
        ...  # resolved without both fields -> unknown; unknown -> identity stripped
```

`ActionItem` gains, all optional with defaults:

```python
source_text: str | None = None  # verbatim span, provenance only
references: list[Reference] = Field(default_factory=list)
```

`description` keeps its name and becomes the **rewritten, self-contained**
statement. That choice is deliberate: `description` is what `build_card`,
`write_action_items`, and `PriorMeetingSource` already read, so no downstream
code has to learn a new field name to show the better text.

Three notes for whoever implements this:

- **Optional-with-defaults is not a style preference.** `FrozenModel` is
  `extra="forbid"`, and every `ActionItem(...)` construction site in
  `test_delivery.py`, `test_commitment_filtering.py`, and `test_orchestrator.py`
  would otherwise break. It is also what makes the rollout order in §8 safe.
- **The validator never raises — on structure or on confidence.** A response
  schema cannot express "email is required only when status is resolved"
  (`required` is `["mention", "turn_ref", "status"]` and cannot be conditional),
  so the model is free to emit either inconsistency; and the prompt hands it a
  tool result containing an email right before telling it to omit one. A raise
  fails `MeetingInsights` validation, which fails extraction, which loses the
  entire meeting — every owner, every item — over one pronoun. So a
  half-identified reference is normalized to `unknown` rather than rejected, and
  confidence gating is a *demotion* that lives in §4 with the attendee check.
  `ground_references` is the enforcement of invariants 5 and 6, so a permissive
  parse costs no safety.
- **`commitment_turn_ref` stays in the schema.** Only its card widget goes
  (§5). It is provenance worth keeping and costs nothing; do not re-litigate.

Have `agent/auth/principal_resolver.py` import `IDENTITY_CONFIDENCE_FLOOR`
instead of its literal `0.85`, so one number governs both owner resolution and
reference resolution. Behaviour is unchanged.

✅ **Check:** `ActionItem(description=..., ...)` with no new fields still
constructs. `Reference(status="resolved", email=None, ...)` comes back
`unknown`; `Reference(status="unknown", email="a@b.c", ...)` comes back with no
email; a `MeetingInsights` payload containing either parses rather than raising.

---

## 2. `resolve_speaker` returns a display name — `agent/tools/speaker_resolution_tool.py`

The model already has everything it needs to *find* a referent: the prompt is
`PipelineRequest.model_dump_json()`, which carries every `transcript_turn` with
its `speaker_name` and `participant_id`, plus the trusted `attendees`. Resolving
"me" to the speaker of that turn is *reading the turn's participant_id*, not
inference — and that id goes through `resolve_speaker`, which already refuses
anything that is not a real attendee.

The one gap is that the tool returns `{email, confidence, method}` and no name,
so nothing downstream can write "Srija Ghosh" into a sentence. Add it:

```python
def _result(
    email: str | None, confidence: float, method: str, display_name: str | None = None
) -> dict[str, Any]:
    return {
        "email": email,
        "confidence": confidence,
        "method": method,
        "display_name": display_name,
    }
```

Each branch passes the matched attendee's `display_name`; the `no_attendees` and
`ambiguous` branches pass `None`.

**Do not add a `resolve_mention` tool.** A new tool would churn
`test_extraction_agent_has_no_context_tools` and
`test_agent_tool_surface_is_read_only` — both pinned A_PLAN invariants — to buy
nothing the existing tool cannot already do.

The unknown case falls out for free rather than needing new logic: turns carry
`participant_id: str | None`, so an unattributable "them" reaches the fuzzy-name
branch, which returns `ambiguous` (confidence `0.0`) or a low score, and §4's
floor turns that into `status="unknown"`. That is exactly the "genuinely
difficult to infer who 'them' is" case the user described.

**The id namespaces do match — verified, not assumed.** `meet_client.py` keys
its attendee map by `participant["name"]` from `conferenceRecords.participants`
and sets `TranscriptTurn.participant_id` from `entry["participant"]`, then looks
the second up in the first to derive `speaker_name`. So the same string that
`resolve_speaker`'s `participant_id` branch matches on is what turns carry, and
a namespace mismatch would already be visible today as every `speaker_name`
reading `"Unknown speaker"`. Two cases still produce no usable id, and both must
fail closed rather than guess: an entry with no `participant` at all
(`participant_id is None`), and an entry whose participant was dropped as
anonymous or directory-unresolvable (id present, not in `attendees`). In both,
the model falls back to `resolve_speaker(speaker_name)`, which for those turns is
`"Unknown speaker"` — no attendee matches, confidence stays under the floor, and
the reference lands as `unknown`. That is the correct outcome.

✅ **Check:** `test_speaker_resolution.py::test_participant_id_match` asserts
exact dict equality and **will fail** — update it and its siblings to expect
`display_name`. That failure is the test doing its job; do not weaken the
assertion to `>=` subset matching.

---

## 3. Extraction prompt — `agent/prompts/extraction_prompt.py`

Two requirements, kept explicitly separate because they fail separately.

**(a) Rewrite, don't slice.** The user's first complaint was that the string is
verbatim transcript. "the request the support request which I raised" is ASR
stutter; substituting names into it still leaves a bad sentence. The prompt must
require a single, self-contained imperative sentence, and require the verbatim
span to be preserved in `source_text` rather than in `description`.

**(b) Resolve every person-reference.** Add rules covering all three forms
present in the user's own example:

- first person (`me`, `I`) → the speaker of that turn, via `resolve_speaker`
  with that turn's `participant_id`; when the turn has no `participant_id`, fall
  back to its `speaker_name` and accept whatever the tool returns, including
  nothing;
- possessive (`my device` → `her device`) — the screenshot contains four of
  these, and a rule about bare pronouns alone would miss them;
- second person (`you`, `your`) → the addressee, which for an assignment is the
  item's already-resolved owner;
- third person (`him`, `her`, `them`, a bare first name) → `resolve_speaker`
  with the spoken name.

**Descriptions are always written in the third person, naming people.** Never
"you" or "your", even for the owner's own item — one `description` is written
once, stored in `action_items` under an attendee-wide `visible_to`, and
delivered to every owner it concerns. Per-recipient phrasing is not something
the schema can represent, so "send your draft" becomes "send her draft" on
Sarah's own card.

For every such mention the model emits one `Reference` with the mention exactly
as spoken, the turn it was spoken in, and the tool's `email`, `display_name`,
and `confidence` copied verbatim — the same "copy it exactly, never invent it"
rule the prompt already carries for owner emails. When the tool cannot identify
the referent, the model emits `status="unknown"` with no identity **and leaves
the original word in the description**, so the sentence still reads and the card
can flag it.

Illustrative before/after to put in the prompt as a worked example:

| | |
|---|---|
| turn 4, Srija | "can you follow up with me about the support request I raised yesterday for my device" |
| `description` | Follow up with Srija Ghosh about the support request she raised yesterday for her device, which is not working. |
| `source_text` | follow up with me regarding the request the support request which I raised yesterday for my device |
| `references` | `me`/turn 4 → resolved Srija Ghosh; `I`/turn 4 → resolved Srija Ghosh; `my`/turn 4 → resolved Srija Ghosh |

The enrichment prompt is **not** touched.

✅ **Check:** `make eval` (gated on credentials) passes the new cases in §7.

---

## 4. Ground the references in code — `agent/auth/reference_grounding.py` (new)

The prompt asks; this enforces. One pure function, no I/O, mirroring
`resolve_principal`'s fail-closed shape:

```python
def ground_references(item: ActionItem, attendees: Sequence[Attendee]) -> ActionItem:
    """Keep only references to real attendees; demote everything else to unknown."""
    by_email = {a.email.strip().casefold(): a for a in attendees}
    grounded: list[Reference] = []
    for reference in item.references:
        attendee = by_email.get((reference.email or "").strip().casefold())
        if (
            reference.status is ReferenceStatus.RESOLVED
            and attendee is not None
            and reference.confidence >= IDENTITY_CONFIDENCE_FLOOR
        ):
            # The name is Meet's, not the model's (invariant 6).
            grounded.append(
                reference.model_copy(
                    update={"email": attendee.email, "display_name": attendee.display_name}
                )
            )
        else:
            grounded.append(
                Reference(
                    mention=reference.mention,
                    turn_ref=reference.turn_ref,
                    status=ReferenceStatus.UNKNOWN,
                )
            )
    return item.model_copy(update={"references": grounded})
```

Call it in `run_pipeline` immediately after `extract(request)`, over every item,
before the actionable/owner grouping — one call site, applied to everything,
independent of ownership.

**What is deliberately *not* checked: the prose.** A validator asserting that a
resolved `display_name` appears in `description` fails correct output — "Srija
Ghosh" resolved, "Follow up with Srija" written — and prose validation that
produces false failures is worse than none. The hard check belongs on the
identity that has consequences, which is the structured reference.

The residual risk this leaves is honest and worth writing down: if the model
resolves "him" to the wrong attendee, `ground_references` cannot tell (the email
*is* a real attendee), and the description keeps the wrong name. What it does
catch is every invented, hallucinated, or low-confidence identity, and those
surface to the reader as an `Unidentified` note (§5) sitting next to prose that
claims certainty — a visible contradiction rather than a silent one.

✅ **Check:** `tests/unit/test_reference_grounding.py` — a reference to a
non-attendee email demotes; a `0.7`-confidence resolved reference demotes; a
model-supplied `display_name` that disagrees with the attendee record is
overwritten from Meet; `mention` and `turn_ref` survive demotion.

---

## 5. Card rendering — `services/ingestion/weave_ingestion/delivery/base.py`

`build_card`'s widget list becomes, per item, in order:

| Widget | Condition |
|---|---|
| `Action` | always (`item.description`) |
| `Status` | always |
| `Deadline` | `item.deadline is not None` (unchanged) |
| `Unidentified` | at least one `Reference` with `status == unknown` |
| `Related context` | `bundle.enriched` **and** `enriched_item.matches` |

Drop the `Commitment turn` widget entirely.

The `Unidentified` text is code-generated, never model prose — one line per
unknown mention, e.g. `"them" (turn 7) could not be identified from the
transcript`.

**Collapsing "unavailable" and "no matches" into one omitted widget is the
user's explicit product call** ("if no related context was present, we do not
need to add that in the card"). Record what that costs: the enriched /
unenriched distinction then survives **only in orchestrator logs** —
`write_action_items` persists neither `enriched` nor `skip_reason`, so do not
tell a future reader they can recover it from Firestore.

✅ **Checks in `tests/unit/test_delivery.py`:**
- `test_build_card_renders_card_v2_contract` — drop the `"3" in widget_texts`
  assertion (that was the commitment turn); assert no widget has that label.
- `test_unenriched_and_no_match_states_are_distinct` — replace with
  `test_related_context_is_omitted_when_there_is_nothing_to_show`, asserting the
  label is absent in **both** the unenriched and empty-matches bundles.
- New `test_unidentified_mentions_are_shown_only_when_present` (invariant 8).

---

## 6. Enrichment may add matches only — `agent/agents/orchestrator.py`

Adding fields to `ActionItem` widens the surface for the enrichment model to
fail to echo an item byte-identically, and today `enforce_owner_scope` builds the
delivered bundle *from that echo*. Two consequences to close.

**(a) Deliver the original, not the echo.** Keep the fingerprint as the
accounting and anti-fabrication gate; change what gets built:

```python
if not remaining_items.get(fingerprint):  # not a Counter any more: absent key
    logger.warning("dropping out-of-scope enriched item")
    continue
original = remaining_items[fingerprint].pop()
accepted.append(EnrichedActionItem(item=original, matches=enriched_item.matches))
```

`remaining` becomes a fingerprint → list-of-originals map instead of a `Counter`
(originals sharing a fingerprint are identical by construction, so popping any
is correct). Translate the guard, do not copy it: today's `remaining[fingerprint]
<= 0` relies on `Counter` returning `0` for an absent key, and the same
expression against a plain dict raises `KeyError` on precisely the fabricated
item the gate exists to reject. The owner check still runs against `enriched_item.item.owner_email`;
multiplicity accounting is unchanged. This immunises every field, present and
future, against enrichment drift — the enrichment model has no business
restating the item, only producing matches for it.

**(b) Never deliver an empty card.** If every item drops on fingerprint
mismatch, today's code emits an `enriched=True` bundle with zero items — a card
with no content. Add: when `scoped_items` is empty and `owner_items` is not,
fall back to `_unenriched_bundle(..., "enrichment_echo_mismatch")`. The reader
gets their action items without context rather than an empty card.

This step is scope-adjacent rather than NER proper; it is here because §1's new
fields are what make the failure likely enough to matter.

**Deliberately not done:** changing `OwnerItemList` so enrichment references
items by index instead of echoing them, which would remove echo drift outright.
It is the better end state, but it moves the enrichment schema, prompt,
callbacks, and the leakage eval set — too much blast radius to attach to this
change. Note it as the follow-up.

✅ **Checks in `tests/unit/test_orchestrator.py`:** an enrichment result whose
echoed item has a reworded `description` is still dropped (fingerprint gate
holds); an item echoed faithfully is delivered as the **original object**
(assert on a field the echo could not have produced); an all-dropped owner
yields `enriched=False, skip_reason="enrichment_echo_mismatch"` with every item
present.

---

## 7. Persistence and eval sets

**`write_action_items`** (`firestore_client.py`) gains two fields per document:
`source_text`, and `references` as `[r.model_dump(mode="json") for r in ...]`.
Purely additive — `PriorMeetingSource` reads `description` and is unaffected, and
documents written before this change simply lack the keys.

**`eval/extraction_cases.json`** gains three cases, following the existing
case shape exactly (`session_input.state.attendees` + a JSON-string
`user_content`):

| `eval_id` | Transcript | Expected |
|---|---|---|
| `first_person_reference_resolved` | Srija: "can you follow up with me about the support request I raised yesterday for my device" / Pritam: "yes, I'll do that" | accepted task owned by Pritam; description names Srija and uses "her device"; three resolved references; no unknowns |
| `unidentifiable_third_person` | "Pritam, can you loop them in before Friday?" / "sure, will do" — no antecedent anywhere | accepted task; description retains "them"; one reference `mention="them"`, `status="unknown"` |
| `second_person_is_the_owner` | "Sarah, can you send your draft to the group?" / "yes" | description reads "send her draft", owner Sarah, `your` resolved to Sarah |

✅ **Check:** `make test` green; `make eval` green where credentials exist.

---

## 8. Rollout

Order is not arbitrary — **ingestion first, agent second**:

1. `make lint && make test`, commit.
2. `make build-image` → set `image_tag` in `infra/deployed.auto.tfvars` →
   `tofu plan -out=rollout.tfplan` → the user runs `tofu apply rollout.tfplan`
   (applies are blocked for the agent in this environment; produce the plan file
   and hand it over).
3. `make deploy-agent`. If it returns a new `AGENT_ENGINE_ID`, update
   `agent_engine_id` in `deployed.auto.tfvars` and run a second
   plan/apply cycle.

Reversing steps 2 and 3 breaks every meeting: a new agent emits `references`,
old ingestion parses the response through `PipelineResult.model_validate`, and
`extra="forbid"` rejects it. In the correct order both intermediate states are
safe — new ingestion + old agent simply sees items with no references and
renders exactly today's card minus the two removed widgets.

**Live verification.** The natural acceptance test is the meeting that produced
the screenshot, re-run through the new pipeline:

- delete `processed_meetings/<conference-record-id>` (the idempotency lease —
  without this the replay acks as already-processed and writes nothing),
- republish that transcript name to `meet-artifacts` as in `SETUP.md` §8,
- confirm the new card for both `me@pmukherjee.dev` and `srija@pmukherjee.dev`:
  description names Srija instead of saying "me", no `Commitment turn` row, no
  `Related context` row.

Then watch the orchestrator logs for `dropping out-of-scope enriched item` over
the next few real meetings. A rise in that line is the signal that §6(a)'s
fingerprint gate is rejecting echoes, and the follow-up in §6 becomes worth
doing.

**Model note (non-blocking):** `gemini-2.5-flash` may rewrite less cleanly than
it slices, since rewriting is a harder task than copying. `WEAVE_MODEL` is the
knob (`agent/config.py`); change it only if the eval cases in §7 actually fail,
not pre-emptively.

---

## 9. Documentation

Add two rows to `infra/SETUP.md`'s trap table once this ships:

| Symptom | Cause | Fix |
|---|---|---|
| Cards deliver with a header and no items | enrichment echoed the item inexactly, so the fingerprint gate dropped everything | §6 falls back to an unenriched bundle; check the orchestrator's `dropping out-of-scope enriched item` warnings |
| Every meeting fails at `PipelineResult.model_validate` right after an agent deploy | a schema-widening agent was deployed ahead of ingestion | deploy ingestion first (§8); redeploy it now to recover |
