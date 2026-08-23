# PLAN — Presentable cards, meeting agenda, and context the agent actually reasons about

Three changes, driven by the delivered card and the target mockup:

1. **Retrieval gets semantic, and the agent gets judgment.** One-sentence lexical
   matching finds "documentation"↔"document" and misses "reimbursement"↔"expense
   claim". Prior items move to embedding search. Recall alone is not the goal:
   many candidates will be old, done, or superseded, so the agent must decide
   which still matter *now* rather than using whatever came back.
2. **Related context stops being a card row.** Nothing retrieved is shown to the
   reader. It is raw material the agent uses to write a short **Details**
   paragraph under each action.
3. **The card is rebuilt to the mockup**: header with meeting agenda, time and
   participants; numbered items; a one-line title; collapsible Details; a status
   chip; ✓/✕ icon buttons (no-op for now); a "Only visible to you" footer line.
   Where an entity could not be identified, the title must simply avoid it — no
   "she", no "them".

Written for an implementing model: follow in order, run every ✅ before moving
on. `A_PLAN.md`'s ground rules hold (Python 3.12, hermetic unit tests, fail
closed, `make lint && make test` green after each step).

---

## 0. Facts established before planning (do not re-derive, do not assume more)

| Claim | Status |
|---|---|
| Card v2 `Section` supports `collapsible` + `uncollapsibleWidgetsCount` — this is the native "Show more / Show less" | verified in the Chat cards reference |
| `Button` supports `altText` (the hover text), `icon.materialIcon`, and icon-only buttons (omit `text`) | verified |
| `DecoratedText` supports `startIcon`, `topLabel`, `bottomLabel`, `wrapText`, and one trailing `button` | verified |
| `fixedFooter` is **not** available for Chat card messages (dialogs/add-ons only) | verified — the footer line must be an ordinary widget |
| A **Pub/Sub** Chat app cannot respond synchronously and cannot update a single card; it must patch the whole message via the API | verified — governs §8 |
| Firestore `find_nearest` supports a pre-filter (`array_contains`) when a composite vector index covers both fields; ≤ 2048 dimensions; COSINE/DOT_PRODUCT/EUCLIDEAN | verified |
| `google_firestore_index` supports `vector_config { dimension flat {} }` | verified |
| `gemini-embedding-001` defaults to 3072 dims and accepts `output_dimensionality` | verified — must be reduced to fit Firestore |
| The Meet API exposes **no** meeting title/agenda field anywhere | verified |
| A Meet transcript's Drive doc is named `"<meeting title> (2022-9-13 at 10:00 PST) - Transcript"`, and `Transcript.docsDestination` points at it | verified — this is the agenda source in §6 |
| `drive.readonly` is already in this deployment's domain-wide delegation grant | verified in `infra/SETUP.md` §5 — no admin console step needed |

Two things deliberately **not** established, called out where they land: whether
the transcript doc is readable by a non-organizer participant (§6), and what
Chat displays when a Pub/Sub app ignores a button click (§8). Both have a
prescribed fallback; neither blocks the rest.

---

## 1. Invariants this adds

Continuing A_PLAN §0 and `PLAN_better_NER.md` §0 numbering.

| # | Invariant | Enforced by (code) | Pinned by (test) |
|---|---|---|---|
| 9 | Retrieved context is **never rendered**. The card shows only agent-written `title`/`details` | `build_card` reads neither `matches` nor `ContextMatch` | `test_delivery.py::test_retrieved_context_is_never_shown_to_the_reader` |
| 10 | Display text is **additive**: `title`/`details` live on `EnrichedActionItem`, never on the extraction `ActionItem` (invariant 7 survives) | schema placement + `enforce_owner_scope` | `test_orchestrator.py::test_enrichment_cannot_alter_the_delivered_item` (existing, must keep passing) |
| 11 | The ACL stays **in the query**: vector search keeps the `visible_to` pre-filter, and a filterless `find_nearest` is never issued | `PriorMeetingSource.search` | `test_context_source_registry.py::test_vector_search_keeps_the_acl_prefilter` |
| 12 | A card never renders an empty shell: missing `title`/`details` fall back to the item's `description` | `build_card` | `test_delivery.py::test_card_falls_back_to_the_extracted_description` |

Invariant 12 is what makes the rollout in §9 safe: new ingestion renders old
agent output unchanged.

---

## 2. Schema — `shared/weave_common/schemas.py`

All new fields optional with defaults, for the same two reasons as last time
(`extra="forbid"`, and every existing construction site keeps working).

```python
class ContextMatch(FrozenModel):
    ...  # existing fields unchanged
    occurred_on: date | None = None  # meeting date of the prior item, for staleness judgement


class EnrichedActionItem(FrozenModel):
    item: ActionItem
    matches: list[ContextMatch] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=160)
    details: str | None = Field(default=None, max_length=700)


class PipelineRequest(FrozenModel):
    ...  # existing fields unchanged
    meeting_title: str | None = None  # agenda, from §6
    started_at: datetime | None = None  # conference start, for the card header
```

`max_length` is a real guard, not decoration: `details` is model prose rendered
into a card, and an unbounded string is how one item swallows the message.
Pydantic raises on overflow, which for enrichment output means that owner
degrades to an unenriched bundle rather than shipping a wall of text — the
existing `except Exception` in `run_pipeline` already handles it.

✅ **Check:** existing `EnrichedActionItem(item=...)` still constructs; a
701-character `details` raises; `PipelineRequest` without the new fields still
validates.

---

## 3. Embeddings at write time — ingestion

**New `services/ingestion/weave_ingestion/embeddings.py`:**

```python
MODEL = "gemini-embedding-001"
DIMENSIONS = 768  # Firestore caps vectors at 2048; 768 is the standard reduced size


def embed_documents(texts: Sequence[str]) -> list[list[float]]: ...  # task_type RETRIEVAL_DOCUMENT
def embed_query(text: str) -> list[float]: ...  # task_type RETRIEVAL_QUERY
```

Build the client lazily inside the call (the module must import without
credentials, like `model_armor.py` and `google_auth.py` do). Both sides — the
ingestion writer and the agent reader — must use the **same model, the same
`output_dimensionality`, and the matching task type**; a mismatch silently
degrades similarity instead of erroring, so keep the constants in one module and
have the agent import its own copy with the same values recorded in a comment
pointing here.

**`firestore_client.write_action_items` stores, per document:**
- `embedding`: `Vector(...)` over `f"{title or description}\n{details or ''}"` —
  embed what a future reader would search for, which is the written form, not
  the raw transcript slice. This is circular on the way in and that is expected:
  the first items embed from `description` alone, because the enrichment that
  writes `title`/`details` had nothing to retrieve yet. The corpus improves as
  it fills;
- `title`, `details` (nullable), and `meeting_date` (needed by §5's staleness
  judgement; it is not stored today);
- everything it already writes.

**Embedding failure must never cost a delivery.** Wrap the embed call: on
failure, log and write the document without `embedding`. The item stays visible
in history and in the lexical fallback; only vector recall misses it, and the
backfill in §4 can repair it later.

✅ **Checks** (`tests/unit/test_onboarding_store.py`, fake embedder injected):
a document carries a `Vector` of length 768 and the new fields; an embedder that
raises still writes the document, without `embedding`; the embedded text is the
written title/details, not `source_text`.

---

## 4. Vector index and backfill

**`infra/firestore.tf`** — alongside the existing composite index:

```hcl
resource "google_firestore_index" "action_items_vector" {
  collection = "action_items"

  fields {
    field_path   = "visible_to"
    array_config = "CONTAINS"
  }
  fields {
    field_path = "embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }
}
```

The pre-filter field must be in the same index as the vector field or
`find_nearest` fails at query time. Keep the existing `visible_to` +
`created_at` index: §5's fallback path still uses it.

**`scripts/backfill_embeddings.py`** (+ `make backfill-embeddings`): stream
`action_items`, skip documents that already have `embedding`, embed in batches,
`update()` each. Idempotent and re-runnable.

State plainly in the script's docstring and in SETUP: **documents without an
embedding are invisible to vector search.** Every item written before this
change is in that state until the backfill runs, so the backfill is part of the
rollout, not an optional tidy-up.

✅ **Checks:** `tofu plan` converges; index reaches READY
(`gcloud firestore indexes composite list`); after backfill, no `action_items`
document lacks `embedding`.

---

## 5. Semantic retrieval, with the lexical path kept as a fallback — agent

Rewrite `PriorMeetingSource.search`:

```python
def search(self, query, principal, limit=5):
    try:
        return self._semantic(query, principal, limit)
    except Exception:  # index missing, embedding quota, API change
        logger.exception("vector search failed; falling back to lexical ranking")
        return self._lexical(query, principal, limit)
```

`_semantic`:

```python
snapshots = (
    self.client.collection("action_items")
    .where(filter=FieldFilter("visible_to", "array_contains", principal.email))  # invariant 11
    .find_nearest(
        vector_field="embedding",
        query_vector=Vector(embed_query(query)),
        distance_measure=DistanceMeasure.COSINE,
        limit=RECALL,  # 20: recall wide, let the agent judge
        distance_result_field="vector_distance",
    )
    .stream()
)
```

Each result becomes a `ContextMatch` with `score = max(0.0, 1.0 - distance)`,
`occurred_on` from the stored `meeting_date`, `title`/`snippet` from the stored
`title`/`description`.

**The recall number has one home.** `registry.search_all` currently defaults to
`limit=5` and passes it to every source; a source that quietly returns 20
anyway would be overriding its caller and stuffing the model's context behind
its back. So: `search_related_context_tool._search` passes `limit=RECALL` (20)
explicitly at the call site, `_semantic` honours whatever `limit` it is given,
and `_lexical` honours `min(limit, LEXICAL_CAP)` with `LEXICAL_CAP = 5` — a
lexical tail is noise, not recall, and the fallback should stay narrow. No
default anywhere is 20; the tool is the only place the number appears.

Pin this end to end, not just at the source: a test that calls the *tool* and
gets 20 candidates back. Without it, "recall wide, let the agent judge" silently
ships as recall of five.

`_lexical` is today's implementation unchanged (window + `rank()`), so
`agent/context_sources/relevance.py` and its tests stay exactly as they are.
Log which path served each search: an unnoticed permanent fallback is the
failure mode that makes this whole step pointless.

The current-meeting exclusion in `search_related_context_tool._search` is
unchanged and still applies to both paths.

✅ **Checks** (`tests/unit/test_context_source_registry.py`, fake client):
`find_nearest` is only ever reached through a `where(...)` (invariant 11);
distance maps to score and `occurred_on` is populated; a client whose
`find_nearest` raises falls back to lexical results rather than returning
nothing; the embedder is never called for a query with no content.

---

## 6. Meeting agenda and participants — ingestion

The Meet API has no title, but the transcript's Drive document is named after
the meeting. Since `drive.readonly` is already delegated, this needs no admin
console change.

**`main._build_live_source`** gains a `build_drive_service(subject)` alongside
`build_meet_service`, using `DRIVE_SCOPE =
"https://www.googleapis.com/auth/drive.readonly"` and the same delegated
credentials, injected into `LiveMeetArtifactSource`.

**In `LiveMeetArtifactSource.fetch`**, after the transcripts list:

```python
document_id = (transcripts[0].get("docsDestination") or {}).get("document")
meeting_title = self._meeting_title(document_id)  # None on any failure
```

`_meeting_title` calls `files().get(fileId=..., fields="name",
supportsAllDrives=True)` and parses:

```python
# "Weekly support sync (2026-08-23 at 22:02 GMT+5:30) - Transcript"
name = name.removesuffix(" - Transcript")
title = name.split(" (")[0].strip() or None
```

Wrap the whole thing in try/except returning `None`: **the agenda is a nicety
and must never fail a meeting.** `started_at` comes from the conference record's
`startTime`, which `fetch` already reads for `meeting_date`.

**Unverified, with its fallback stated:** the transcript document lives in the
organizer's Drive, and Weave impersonates *the subscribing participant*, who may
not have been granted access — expect a 403/404 for meetings the recipient did
not organize. Step 1 of implementation is a probe against the four real
conference records; if access proves unreliable, the header simply omits the
agenda line (the card must render correctly without it), and the Calendar
route — matching `conferenceData.conferenceId` to the Meet meeting code, which
needs a **new** `calendar.events.readonly` DWD scope — becomes a follow-up, not
part of this plan.

✅ **Checks** (`tests/unit/test_meet_client.py`): the three title-parse cases
above; a Drive client that raises leaves `meeting_title` None and still returns
a valid `PipelineRequest`; no Drive call at all when `docsDestination` is
absent.

---

## 7. The card — `services/ingestion/weave_ingestion/delivery/base.py`

Signature becomes `build_card(bundle, meeting: MeetingHeader | None = None)`,
where `MeetingHeader` is a small ingestion-side dataclass (`title`, `started_at`,
`participant_names`) built in `main.py` from the `PipelineRequest` that
ingestion already holds. Nothing about the header needs to travel through the
agent.

**Structure** (each row below is one widget, in order):

```
header:  title    "Action items for you"
         subtitle "<agenda> • <HH:MM>"        (agenda omitted if None → just the time)
         imageUrl <optional https icon; omit rather than invent one>

section 0, uncollapsible:
    decoratedText  startIcon materialIcon "group"
                   text "with Srija, Pritam, and 3 more"     (omit section if no others)

section per item i (1-based), collapsible = true, uncollapsibleWidgetsCount = 1:
    decoratedText  startIcon materialIcon "check_circle" | "schedule"
                   topLabel "Accepted by you" | "Reassigned to you" | "Awaiting your response"
                   text     "<i>. <title or item.description>"
                   wrapText true
    decoratedText  topLabel "Details"
                   text     "<details>"                       (widget omitted when no details)
                   wrapText true
    buttonList     [ ✓ materialIcon "check", altText "Accept"
                     ✕ materialIcon "close", altText "Decline" ]

final section:
    decoratedText  startIcon materialIcon "lock"
                   text "Only visible to you"
```

Notes that matter:

- `uncollapsibleWidgetsCount = 1` puts exactly the title row above the fold;
  Chat renders "Show more"/"Show less" itself. Do not hand-roll it.
- The status chip maps from `item.status`: `accepted` → "Accepted by you",
  `reassigned` → "Reassigned to you", anything else → "Awaiting your response"
  (unreachable today — only actionable statuses are delivered — but the card
  should not lie if that changes).
- **Deleted:** the `Commitment turn` row (already gone) and the
  `Related context` row. The `Unidentified` row survives in exactly one place —
  see the next bullet.
- **The fallback path can still show a pronoun, and must admit it.** When
  enrichment fails, invariant 12 renders `item.description`, which is the
  pre-grounding extraction text: the last live replay produced
  `"...inform Lisa about her work."` on an item whose references came back
  `unknown=['Lisa', 'she']`. §8's rule cannot help there, because no title was
  written. So when an item has **no `title`** and has unknown references, the
  card appends the `Unidentified` widget for that item only. The normal path
  never shows it (the agent wrote around the unknown entity); the degraded path
  explains its own pronoun instead of quietly getting it wrong.
- **Verify every `materialIcon` name before shipping it.** `materialIcon` is
  supported, but `"group"`, `"schedule"`, `"check_circle"`, `"lock"`, `"check"`,
  `"close"` are plausible-looking guesses at Material Symbols names, and an
  invalid name fails the whole card render rather than dropping one icon. Check
  each against the Material Symbols set; if one is rejected, omit that icon
  rather than substituting another guess.
- Buttons carry `onClick.action.function` `"accept_item"` / `"decline_item"`
  with parameters `conference_id` and `item_index`, so the handler in §8 has
  something to log and a later change has somewhere to write.
- Participants line: display names from the meeting's attendees, excluding the
  recipient, first two by name plus "and N more".

✅ **Checks** (`tests/unit/test_delivery.py`): every per-item section has
`collapsible: true` and `uncollapsibleWidgetsCount: 1`; items are numbered from
1; `title`/`details` render, and with both absent the card falls back to
`item.description` with no Details widget (invariant 12); an untitled item with
an unknown reference gets the `Unidentified` widget, and a titled one never
does; no widget text ever contains a `ContextMatch` title or `source_name`
(invariant 9); both buttons carry `altText`; the header renders correctly with
`meeting=None`.

---

## 8. What the agent writes — enrichment prompt and scope

The enrichment agent now produces three things per item: `matches` (internal),
`title`, `details`.

**Judging relevance.** The tool returns up to 20 candidates, each with
`occurred_on` and `score`. The prompt must state that recall is deliberately
wide and most candidates are noise: keep only what bears on this item *now*.
Spell out the disqualifiers — an item that was already completed, one about a
different request that merely shares people, and one old enough that its state
is unknowable are all irrelevant. Relative age is available from `occurred_on`
and the meeting date, and the model must weigh it rather than treat a high
similarity score as sufficient.

**Writing the title.** One line, imperative, no trailing detail. If any of the
item's `references` has `status: "unknown"`, the title must not lean on that
entity at all — not "her", not "them", not a guessed name. Rewrite around it
("Complete the documentation work and share it with the requester") or simply
drop the unresolvable clause: a short, honest title beats a precise-sounding
wrong one. This replaces the Unidentified widget entirely.

**Writing the details.** One to three sentences. When related context survived
the judgement, use it to say what the reader needs to know — what happened
before, what is outstanding, what to do. When nothing survived, write the plain
restatement of the action and nothing more. Never state a fact that is in
neither the item nor a kept match.

**`enforce_owner_scope`** carries `title` and `details` across alongside
`matches`, still pairing them with the *original* item (invariant 10). Truncation
is not needed — §2's `max_length` makes an over-long field a validation error,
which degrades that owner to unenriched rather than shipping it.

**Decision point, with a trigger.** The scope gate still fingerprints the
model's echo of the whole `ActionItem`, and this step asks the model to write
two prose fields in the same response — tokens it is no longer spending copying
`description`, `source_text`, and `references` byte-exactly. A drifted echo
drops the item into `enrichment_echo_mismatch`, which now means a card with no
title and no details. `PLAN_better_NER.md` §6 already names the fix and defers
it: have `OwnerItemList` reference each input item by index instead of restating
it. Do it **before continuing** if the §10 replay shows either
`enrichment_echo_mismatch` or a rise in `dropping out-of-scope enriched item`.
That is the trigger; do not pre-emptively rebuild the schema without it.

**Persistence:** `write_action_items` stores `title` and `details` (§3), so the
next meeting's retrieval reads the written form rather than the raw description.

✅ **Checks:** an enrichment result carrying `title`/`details` reaches the bundle
with the original item object unchanged; over-long `details` degrades that owner
to an unenriched bundle instead of raising out of the pipeline; new eval cases
for (a) a stale candidate correctly rejected, (b) a genuinely related candidate
used in `details`, (c) an item with an unknown reference whose title contains no
pronoun for it.

---

## 9. Button clicks — `/chat-events`

The buttons are no-ops for now, but the click still arrives as an interaction
event on the Chat Pub/Sub topic, so the handler must deal with it deliberately.

`parse_chat_event` already ignores unknown payloads and acks; extend it only so
far as recognising a card click (`buttonClickedPayload` in the add-on envelope,
`CARD_CLICKED` in the classic one) and logging `function`, `conference_id`,
`item_index`, and the clicking user. Do **not** let a click reach the onboarding
upsert.

**The one unknown, and its fallback.** A Pub/Sub Chat app cannot answer an
interaction synchronously and cannot update a single card — the documented route
is patching the whole message via `spaces.messages.patch`. What Chat shows the
user when the app simply acks and does nothing is not documented; it may be
silent, or it may show a transient failure. So: implement the ack, then click
the button on the real app and look. If it reads as an error, the fallback is
already scoped — patch the message with the identical card, which is also the
exact mechanism the eventual accept/decline state will use.

✅ **Checks:** a card-click event never onboards a user and never reaches
`upsert_onboarded_user`; the click is logged with its parameters; the endpoint
returns 200.

---

## 10. Rollout

Order, and why:

1. `make lint && make test`, commit.
2. **Index first.** `tofu plan -out=rollout.tfplan` for the vector index → user
   applies → wait for READY. A `find_nearest` against a missing index fails, and
   while §5 falls back to lexical, the point is to not ship into the fallback.
3. **Ingestion second.** `make build-image` → `image_tag` in
   `deployed.auto.tfvars` → plan → user applies. New ingestion writes
   embeddings, and renders old agent output unchanged via invariant 12.
4. `make backfill-embeddings`, so existing history becomes searchable.
5. **Agent last.** `make deploy-agent` → new engine id → tfvars → plan → user
   applies. Reversed, a new agent emitting `title`/`details` hits old
   ingestion's `extra="forbid"` and every meeting fails.

**Verification** is the same replay as before — clear
`processed_meetings/<id>`, republish with the `ce-subject` attribute — with
these specific things to read on the resulting card:

- the header shows the agenda and the participants line (or degrades cleanly if
  the transcript doc was not readable);
- items are numbered, collapsed to their title, and expand to Details;
- the documentation item's Details reflects the VDI/GCP prior item — that pair
  is the known-good relevance case from the last change;
- no "Related context", "Unidentified", or "Commitment turn" rows anywhere;
- the item with the unknown "Lisa"/"she" reference has a title that names
  neither;
- both buttons render with hover text, and clicking one does not produce an
  error.

Also confirm in the agent logs that vector search served the query rather than
the lexical fallback.

---

## 11. Risks this plan accepts

- **Details can be wrong in a way code cannot catch.** It is model prose about
  real prior work, and the only guards are the prompt, the length cap, and the
  fact that its inputs are ACL-filtered. Retrieval fabrication is not new (the
  model could already invent `matches`), but rendering prose raises the stakes.
  If it misbehaves in practice, the fix is validating that kept matches were
  actually returned by the tool — deferred deliberately, because it needs ADK
  state round-tripping.
- **The agenda may be unavailable** for meetings the recipient did not organise
  (§6). The card is designed to degrade rather than fail.
- **Embedding adds a per-meeting API cost and latency** on the write path, and a
  second call on the read path. Both are small, both are isolated so failure
  degrades rather than breaks.
- **Vector recall is only as good as the backfill.** Skip step 4 of the rollout
  and the system will look like it is working while ignoring all history.
