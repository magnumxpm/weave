# Features

Every entry states the **problem**, the **solution**, and **where it lives**. Features are
grouped by the stage of the journey they serve.

---

## A. Capture — getting the meeting in

### A1 · Zero-touch Meet ingestion
**Problem.** Meeting tools that need a bot in the call change the meeting: someone has to
invite it, everyone sees it, and it fails for the calls nobody remembered to add it to.

**Solution.** Weave never joins a call. Each onboarded user has their *own* Google
Workspace Events subscription on Meet transcripts. When their transcript is ready, Workspace
publishes to a Pub/Sub topic in the project, and Cloud Run picks it up. Nothing to install
beyond a Chat app the user adds themselves, no bot, no calendar changes.

**Where.** `infra/pubsub.tf`, `services/subscription_manager/`, `main.py::pubsub_push`.

### A2 · Per-user subscription lifecycle
**Problem.** There is no organisation-wide Meet subscription; one subscription per user is
the real scaling unit, and Workspace caps them at 7 days. A missed renewal silently drops
someone's meetings — the worst failure mode, because it looks like nothing happening.

**Solution.** A scheduled Cloud Run job reconciles `onboarded_users` against live
subscriptions: creates missing ones, renews any with under 25% of TTL remaining, replaces
deleted ones, and processes `offboarding` tombstones by deleting the subscription *before*
the record. One user's failure never stops the sweep, and their record survives for the next
one. An install also triggers an immediate sweep, so nobody waits for the schedule.

**Where.** `services/subscription_manager/weave_subscriptions/manager.py`.

### A3 · Read as the right person
**Problem.** A Meet conference record is visible **only** to that conference's participants.
A single fixed service subject would restrict the system to one person's meetings — and
reading as the wrong account is a silent authorisation error.

**Solution.** Live reads impersonate the user whose subscription produced the event, taken
from the CloudEvent subject. When that id cannot be determined the event fails loudly with
the full attribute set logged, rather than falling back to a guess. `admin_subject` is used
**only** for Directory lookups, never for Meet.

**Where.** `meet_client.py::extract_subscriber_user_id`, `main.py::pubsub_push`.

### A4 · Deterministic attendee identity
**Problem.** Speaker labels in a transcript are display names, and display names are not
identities.

**Solution.** Meet reports each signed-in participant; the participant's user id resolves to
an address through the Directory API, and every turn is tied to that participant id.
Anonymous and dial-in participants are kept in the transcript — dropping them would change
what was said — but can never own an item.

**Where.** `meet_client.py::LiveMeetArtifactSource`.

### A5 · One unresolvable guest never costs the meeting
**Problem.** External guests are not in the directory, and failing the meeting over one of
them punishes everyone who was.

**Solution.** An unresolved participant is dropped individually with a warning. But if
*every* signed-in participant fails to resolve, that is a broken directory rather than a
room of guests — the meeting fails loudly and is retried.

**Where.** `meet_client.py::LiveMeetArtifactSource.fetch`.

### A6 · The meeting's agenda, free
**Problem.** A card headed "Action items for you" with no context makes the reader work out
which meeting it came from.

**Solution.** The transcript document's Drive filename is the meeting's agenda-like title,
read with the `drive.readonly` scope already granted for context search. The read is
best-effort: if the participant cannot open an organiser-owned document, the card keeps the
time and omits the title rather than failing.

**Where.** `meet_client.py::_meeting_title`, `delivery/base.py::build_card`.

### A7 · Exactly-once meeting processing
**Problem.** Pub/Sub is at-least-once. Reprocessing a meeting would duplicate history and
re-deliver cards.

**Solution.** A Firestore lease claims each conference id before work begins. A duplicate is
acked without processing. Failed or expired-lease meetings are reclaimable, so a crash
mid-flight does not strand a meeting forever.

**Where.** `firestore_client.py::claim_meeting`, `test_duplicate_event_is_acked_without_processing`.

### A8 · A fixture route that exercises the deployed code
**Problem.** A system testable only when fully wired to Workspace is slow to change and
risky to roll out.

**Solution.** `MeetArtifactSource` is a seam with `live` and `fixture` implementations. In
`fixture` mode the deployed service runs the *entire* real path — screening, extraction,
ACLs, reconciliation, delivery — against a bundled transcript, which is what makes a smoke
test after every rollout cost one `gcloud pubsub topics publish`.

**Where.** `meet_client.py`, `services/ingestion/fixtures/`.

---

## B. Verification — the part that makes it trustworthy

### B1 · Commitment verification (assignment ≠ commitment)
**Problem.** Every transcript tool lists "Alex to prepare the checklist" whether Alex agreed,
refused, or said nothing. A list that cannot tell a promise from a suggestion is not
actionable, and gets abandoned.

**Solution.** Five explicit statuses — `accepted`, `declined`, `deferred`, `reassigned`,
`unresolved` — with only `accepted` and `reassigned` treated as actionable. **Silence is
always `unresolved`.** An `accepted` item is structurally required to carry
`resolution_turn_ref`, the turn index where acceptance happened; a pydantic validator rejects
one that does not.

This is enforced in code, never in a prompt.

**Where.** `weave_common/schemas.py`, `agent/auth/redaction.py`,
`tests/unit/test_commitment_filtering.py`.

### B2 · Turn-level provenance
**Problem.** "Trust me" is not evidence. A user who cannot check a claim will not rely on it.

**Solution.** Every item carries `commitment_turn_ref` (where the work was raised) and
`resolution_turn_ref` (where it was settled), plus `source_text` — the verbatim spoken span,
kept alongside the tidied description.

**Where.** `weave_common/schemas.py::ActionItem`.

### B3 · Identity from Meet, never from the model
**Problem.** A model asked "who is Sarah?" will always produce a plausible answer. Acting on
a hallucinated identity means routing someone's work to the wrong person.

**Solution.** `resolve_speaker` matches only against attendee state supplied by the Meet API,
with a graded confidence: participant ID → 1.0, exact display name → 0.95, fuzzy name →
score × 0.9, ambiguous → 0.0 and no email. Two candidates within 0.05 of each other are
`ambiguous`, not a coin flip. Display names come from Meet, never from model output.

**Where.** `agent/tools/speaker_resolution_tool.py`.

### B4 · Fail-closed principal resolution
**Problem.** A partially-resolved owner is the dangerous case: confident enough to look
right, wrong enough to leak.

**Solution.** `resolve_principal` refuses on three distinct grounds — `no_email`,
`low_confidence` (below 0.85), `not_attendee` — and a refusal produces an **unenriched bundle
carrying its reason**, never a search. A speaker who appears in the transcript but not in the
attendee list is refused outright.

**Where.** `agent/auth/principal_resolver.py`,
`test_transcript_only_attendee_is_refused_principal`.

### B5 · Reference grounding
**Problem.** Pronouns carry identity. "Follow up with me about my device" is useless without
knowing who *me* is — and dangerous if the model guesses.

**Solution.** Extraction emits one `Reference` per spoken mention (first, second, third
person, and bare names) with `turn_ref` preserved. Every reference is then re-grounded in
pure Python against the trusted attendee list; anything not a real attendee, or below the
confidence floor, is demoted to `unknown` with identity fields cleared — while keeping the
mention and turn, so provenance survives.

A half-identified reference is normalised to `unknown` rather than rejected, because raising
would fail the whole meeting's validation over one pronoun.

**Where.** `agent/auth/reference_grounding.py`, `weave_common/schemas.py::Reference`.

### B6 · Unknown identities never reach a title
**Problem.** A confident-sounding title built on a guessed name is worse than a vague one.

**Solution.** If any reference is `unknown`, enrichment must rewrite around the entity or
omit the clause. A short honest title beats a precise-sounding wrong one — and where no
title survives, the card shows the unidentified mention and its turn instead.

**Where.** `agent/prompts/enrichment_prompt.py`, `delivery/base.py::build_card`,
`test_unidentified_mentions_are_only_shown_for_an_untitled_fallback`.

### B7 · Self-contained descriptions
**Problem.** "Send the report" is not a task — you still have to re-read the meeting to know
which report, in what format, by when.

**Solution.** Descriptions must be self-contained imperatives carrying every
transcript-supported constraint, acceptance criterion, implementation detail and reproduction
step needed to do the work — even when discussed in a different turn. ASR stutters are
rewritten rather than pasted. Supporting steps do not become separate items unless
independently assigned and accepted. Descriptions use third-person names, never "you".

**Where.** `agent/prompts/extraction_prompt.py`.

### B8 · Deadline inference with the spoken phrase kept
**Problem.** "By Friday" is meaningless without the meeting date, and a normalised date alone
is unauditable.

**Solution.** A deterministic tool resolves the phrase relative to the meeting date, and
`deadline_source_text` preserves what was actually said. No resolution means `deadline: null`
— never a guess.

**Where.** `agent/tools/deadline_inference_tool.py`.

### B9 · Structured meeting summaries
**Problem.** A prose blob is not retrievable. "What did we decide about the migration?" cannot
be answered from an unstructured paragraph.

**Solution.** Every meeting produces a bounded, transcript-grounded `MeetingSummaryContent`:
`overview`, `topics`, `decisions`, `implementation_notes`, `reproduction_steps`. Categories
not discussed are empty lists — never inferred. Length limits are in the schema.

**Where.** `weave_common/schemas.py::MeetingSummaryContent`.

---

## C. Isolation — per-person by construction

### C1 · One private session per owner
**Problem.** Enriching everyone's items in one context lets one person's private files
inform another person's card — and no prompt reliably prevents it.

**Solution.** Enrichment runs once per owner in a **fresh session**, with `user_id` set to
that owner and session state containing only their items. Isolation is structural, not
instructed.

**Where.** `agent/agents/enrichment.py`,
`test_enrichment_session_state_contains_only_owner_items`.

### C2 · Extraction has no context tools
**Problem.** The one agent that legitimately sees everyone's words must not also be able to
reach anyone's files, or the two capabilities combine into a disclosure path.

**Solution.** The extraction agent's tool list contains exactly `resolve_speaker` and
`infer_deadline`. Asserted directly.

**Where.** `test_extraction_agent_has_no_context_tools`.

### C3 · ACL in the query, not after it
**Problem.** A post-filter is one bug away from a disclosure, and the bug is invisible in
testing if the filter usually works.

**Solution.** Every Firestore context query carries `visible_to array_contains <principal>`
as a query predicate. The database never returns a document the principal cannot see, and
the ACL field shares an index with the vector field so the prefilter survives vector search.

**Where.** `prior_meeting_source.py`, `meeting_summary_source.py`, `infra/firestore.tf`.

### C4 · Agent Engine holds no delegation
**Problem.** Domain-wide delegation on the agent runtime would make every model call a
potential domain-wide read.

**Solution.** `weave-agent-sa` has `aiplatform.user`, `datastore.user`, `modelarmor.user`,
and `run.invoker` on ingestion — and no DWD, ever. Delegated Google reads happen only inside
ingestion, behind an authenticated broker.

**Where.** `infra/iam.tf`, `agent/context_sources/broker_client.py`.

### C5 · The broker refuses non-onboarded subjects
**Problem.** A search executed for someone who never opted in is a read they did not consent
to.

**Solution.** `/context/search` checks the subject against `onboarded_users` and returns
empty **without searching** if absent.

**Where.** `test_context_broker_refuses_non_onboarded_subject_without_searching`.

### C6 · Query text is never logged
**Problem.** Google API errors can embed the request URL — and therefore the search text —
into their message, turning an error log into a content leak.

**Solution.** Broker failures log the subject, source, result count, and the **exception type
only**.

**Where.** `main.py::context_search`.

### C7 · Service-only sources never serve a user query
**Problem.** A source that cannot be scoped to a person would return the same results to
everyone.

**Solution.** Sources declare an `AuthMode`. `SERVICE_ONLY` sources are dropped from a
user-facing registry unless `allow_service_only=True`, which defaults to `False`.

**Where.** `agent/context_sources/registry.py`,
`test_service_only_results_never_reach_the_caller`.

### C8 · Owner-scoped enrichment echo gate
**Problem.** A model returning items can reword, duplicate, drop, or re-own them — and a
re-owned item is a cross-owner leak arriving through the output path.

**Solution.** SHA-256 fingerprint matching with multiplicity, discarding the model's copy and
keeping the original plus only additive display fields. Total mismatch degrades to an
unenriched bundle.

**Where.** `agent/agents/orchestrator.py::enforce_owner_scope`.

### C9 · Owner guards on every commitment read and write
**Problem.** A commitment id guessed or copied from elsewhere — a card button carries ids in
its parameters — must not be readable or closable by another user.

**Solution.** Every `CommitmentStore` read and lifecycle write carries an owner guard;
`close` and `reopen` are owner-scoped and idempotent, and a redrawn card silently omits any
row that is not the clicker's.

**Where.** `weave_ingestion/commitments.py`, `services/chat/weave_chat/main.py`,
`test_close_and_reopen_are_owner_guarded_and_idempotent`.

### C10 · Delivery is per-person by construction
**Problem.** Posting everyone's items to a shared channel is a disclosure and useless noise.

**Solution.** There is no call shape that delivers a whole meeting: a deliverer takes one
owner and one bundle, and re-checks that the bundle and the target both belong to that
owner. Cards go to the DM space recorded at install; Weave never joins a group space.

**Where.** `delivery/base.py`, `test_chat_refuses_a_target_for_another_owner`.

---

## D. Memory — one commitment, many meetings

### D1 · Cross-meeting reconciliation
**Problem.** The same deliverable raised in three meetings becomes three tasks — and the one
fact that matters, *this is the third week*, exists nowhere.

**Solution.** Each new mention is judged against that owner's open commitments and merged
when it is the same concrete deliverable. The commitment carries `first_seen`,
`last_mentioned`, and `mention_count`; each mention is retained as immutable evidence with
its relationship: `original`, `restated`, `carried_over`, `progress_evidence`,
`completion_evidence`.

Same project, person, or topic is explicitly **not** a match. Work spawned after an earlier
deliverable completed is new work.

**Where.** `weave_ingestion/commitments.py`, `prompts/reconcile_prompt.py`.

### D2 · Judgement reads the spoken words
**Problem.** Judging on the tidied description alone loses stated dependencies — they get
paraphrased out. This is why the dependency graph originally had no edges at all.

**Solution.** `judgement_text` assembles description + stated precondition + verbatim
`source_text` + enrichment details, skipping near-duplicates. The richer text is for
judgement only; the stored mention excerpt stays the description.

**Where.** `commitments.py::judgement_text`,
`test_a_stated_precondition_reaches_the_judgement_verbatim`.

### D3 · Thresholds enforced in Python
**Problem.** A model's confidence is a suggestion, and a model can name an id that was never
offered to it.

**Solution.** A merge requires `confidence ≥ 0.80` **and** an id present in the candidate
list. Both checked outside the model.

**Where.** `test_threshold_and_candidate_membership_are_enforced_in_python`.

### D4 · Carry-over made visible
**Problem.** "You've now promised this three times across sixteen days" is the single most
useful thing a commitment tracker can say, and no per-meeting tool can say it.

**Solution.** `carry_over` renders the span and count — and is suppressed when it would only
restate the reason, because repeating an identical phrase reads as a rendering bug.

**Where.** `weave_common/commitment_view.py::_carry_over`.

### D5 · Semantic history with a lexical floor
**Problem.** Keyword search misses paraphrase; a pure vector system goes blind when the index
or embedder is unavailable.

**Solution.** 768-dimensional embeddings with vector search under the ACL prefilter, falling
back to IDF-weighted lexical cosine on failure. The lexical ranker deliberately preserves
source order rather than sorting by length — the most discriminating terms here are acronyms
(VDI, GCP, SRE), which a length ordering would bury.

**Where.** `weave_common/relevance.py`, `agent/context_sources/embeddings.py`.

### D6 · Embedding failures never cost history
**Problem.** An embedding outage that dropped writes would lose the record permanently.

**Solution.** Embedding failure is logged and the document is written lexically. The gap is
repairable with `make backfill-embeddings`.

**Where.** `test_action_item_embedding_failure_never_costs_the_history_write`.

### D7 · Replay-safe identifiers
**Problem.** Re-running a backfill or replaying a meeting must not duplicate anything.

**Solution.** Commitment ids are UUIDv5 over the first mention ref; mention refs are
`{conference_id}--{owner}--{index}`. Every write is a deterministic upsert, and the fold of a
mention into its commitment is one transaction across three documents.

**Where.** `commitments.py::commitment_id_for`, `firestore_client.py::persist_meeting`.

---

## E. Prioritisation — what to do first, and why

### E1 · Dependency graph from stated preconditions only
**Problem.** An inferred dependency corrupts exactly the answer the graph exists to give.
Two items sharing a topic are not a dependency, and guessing makes the ordering worse than
having none.

**Solution.** `blocked_on` is populated only when a turn explicitly states the precondition —
*"I can't send the request until you give me your email"*. The schema documents it as
"only ever a stated precondition, never inferred from two items sharing a topic". At
reconciliation, `blocking_hint` becomes an edge only when it names a candidate, or resolves
to one at `≥ 0.80` similarity. No hint means no edge, even when a close candidate exists.
Every edge stores the mention ref that stated it.

**Where.** `weave_common/schemas.py::ActionItem.blocked_on`,
`test_no_hint_means_no_edge_even_when_a_close_candidate_exists`.

### E2 · Transitive unblock impact
**Problem.** "This is blocking one thing" and "this is blocking one thing that blocks four
more" should not rank the same.

**Solution.** `transitive_dependents` walks the reverse edge set per commitment, counting
everything it transitively frees. It is cycle-safe and ignores closed rows.

**Where.** `commitment_view.py::transitive_dependents`,
`test_dependent_counting_survives_a_cycle_and_ignores_closed_rows`.

### E3 · Deterministic attention scoring
**Problem.** If ordering depends on model mood, the same question gets different answers on
different days and the ranking cannot be trusted or explained.

**Solution.** A pure function: dependents × 200; overdue +1000 + days late; waiting ≥ 7 days
+500 + days; open + 10 × mention count; likely-complete −50. Stated once, in shared code.

**Where.** `commitment_view.py::attention_score`.

### E4 · Urgency grouping with honest reasons
**Problem.** A ranked list without reasons is an oracle. Users cannot correct what they
cannot see the basis for.

**Solution.** Eight ordered groups, each with a `reason` built **only from facts the row
carries** — a row with no deadline is never called overdue.

**Where.** `commitment_view.py::_urgency`, `_reason`,
`test_each_group_states_a_reason_built_only_from_facts_the_row_carries`.

### E5 · Recommendation ≠ diagnosis
**Problem.** "This is overdue" is a diagnosis. "Finish it" is useless advice for work that
cannot proceed.

**Solution.** A separate `recommendation` where being blocked outranks being overdue:
*"Blocked — push on X before this can move"*. Every state gets an actionable next step.

**Where.** `commitment_view.py::_recommendation`,
`test_a_blocked_commitment_is_told_to_push_on_its_blocker_not_to_finish_itself`.

### E6 · One judgement, every surface
**Problem.** A card and a chat answer disagreeing about the same commitment destroys trust in
both.

**Solution.** The judgement lives in `weave_common/commitment_view.py`. Each surface only
chooses how to draw it: the card renders widgets, the copilot reports the `recommendation`
and `attention_reason` strings it was handed rather than composing its own.

**Where.** `shared/weave_common/commitment_view.py`, `test_views_and_card_agree_on_what_is_shown`.

### E7 · Staleness detection
**Problem.** Commitments do not fail loudly. They go quiet and are discovered on the day they
were due.

**Solution.** Anything unmentioned for 14 days surfaces as `stale` with an explicit prompt:
*"Nobody has mentioned this lately — is it still real?"* `find_stale_commitments` exposes it
on demand.

**Where.** `commitment_view.py` (`STALE_DAYS = 14`), `agent/copilot/tools.py`.

---

## F. Delivery and conversation — one Chat DM

### F1 · Self-service onboarding through a Chat install
**Problem.** A per-user product gated on a Terraform allowlist puts an administrator between
every user and the thing they want.

**Solution.** A user opens Chat → New chat → Weave and sends any message. That event records
their numeric Cloud Identity id and their exact DM space, submits an immediate
subscription-manager sweep, and answers with a welcome card. Availability is an administrator
decision; installation is the user's.

**Where.** `main.py::chat_events`, `services/chat/`, `infra/chat_events.tf`.

### F2 · The install grants nothing
**Problem.** An install prompt that appears to grant data access invites the wrong mental
model of what was consented to.

**Solution.** The Chat install is only an opt-in signal. Domain-wide delegation remains the
authority for reading Meet data; adding or removing the app changes no permission, and
offboarding tombstones the record so the sweep can delete the subscription before the record.

**Where.** `infra/SETUP.md` §6, `firestore_client.py::mark_offboarding`.

### F3 · One card per owner, per meeting
**Problem.** Results that arrive as prose are unactionable, and results that arrive in a
shared space are a disclosure.

**Solution.** A direct-message card headed with the meeting's agenda title and time, listing
the other participants, then one collapsible section per item: the enriched title, its
status, details, and buttons. It ends with *Only visible to you*, which is literally true.

**Where.** `delivery/base.py::build_card`, `delivery/chat.py`.

### F4 · Card buttons that actually work
**Problem.** A Pub/Sub-connected Chat app has no channel to answer an interaction on — a
button click never reaches the service and the user sees a red "unable to process your
request".

**Solution.** The App URL connection with **Authentication Audience: HTTP endpoint URL**, so
the bearer is a Google-signed OIDC token verified by the same code path used for Pub/Sub
push. `weave-chat` answers clicks itself and republishes everything else, which makes the
switch reversible with no redeploy. Both Chat envelope dialects — classic and HTTP add-on —
are parsed, and the reply dialect is chosen from the request itself and logged on every call.

**Where.** `services/chat/weave_chat/main.py`, `weave_chat/responses.py`.

### F5 · A click redraws the card from stored state
**Problem.** A button that answers with a sentence leaves the user staring at a card that
still says the opposite.

**Solution.** After a close or reopen, the ids that were rendered are re-read by id under the
owner guard and re-rendered, so the refreshed card is a function of storage rather than of
the click. Rows that are not the clicker's simply drop out.

**Where.** `weave_chat/main.py::_rerender`.

### F6 · Twelve principal-scoped copilot tools
**Problem.** A model answering from its own memory of a conversation invents facts.

**Solution.** Every factual claim comes from a deterministic tool, each scoped to the session
principal: `suggest_next_actions`, `list_my_commitments`, `get_commitment_history`,
`find_stale_commitments`, `trace_blockers`, `search_my_history`, `search_my_meetings`,
`get_meeting_summary`, `list_my_commitment_mentions`, `search_workspace_evidence`,
`close_commitment`, `reopen_commitment`.

**Where.** `agent/copilot/tools.py`, `agent/copilot/agent.py`.

### F7 · Advice vs. inventory
**Problem.** Answering "what should I do?" with the full list makes the user do the
prioritising — which is exactly the work they asked for.

**Solution.** The prompt routes intent explicitly: advice questions call
`suggest_next_actions` (default limit 3) and answer with a recommendation; only inventory
requests call `list_my_commitments`.

**Where.** `agent/copilot/prompt.py`, `test_advice_mode_shows_the_next_step_and_a_plain_list_does_not`.

### F8 · An error is never an absence
**Problem.** A tool returning `[]` on a bad argument is indistinguishable from "you have
nothing to do" — and the model will report absence as fact.

**Solution.** An invalid `status_filter` returns a structured **error row** listing valid
values. The prompt states that the copilot may never claim the user has no commitments
unless `list_my_commitments` with `all` returned empty on that turn.

**Where.** `copilot/tools.py`, `test_all_lists_everything_and_a_bad_filter_is_an_error_not_an_empty_list`.

### F9 · Confirmation-gated closing
**Problem.** Closing the wrong commitment on a vague "yes" destroys the record silently.

**Solution.** `close_commitment` is never called until the user explicitly confirms *that*
commitment. A bare "yes" counts only when the immediately preceding turn proposed closing
exactly one named commitment. Reopen requires an explicit request. The graph distinguishes
human-`closed` from inferred `likely_complete`, and never infers `closed`.

**Where.** `agent/copilot/prompt.py`, `test_reconciler_never_turns_model_inference_into_closed`.

### F10 · Meeting-aware retrieval
**Problem.** "What did we decide about the migration?" is a different question from "what do
I owe?" and needs different data.

**Solution.** `search_my_meetings` (topic- and date-scoped summary search),
`get_meeting_summary` (exact context for a referenced meeting), and
`list_my_commitment_mentions` (what arrived in a given window) — each ACL-filtered. A legacy
meeting with no stored summary is reported as unavailable, never reconstructed.

**Where.** `agent/copilot/tools.py`, `agent/copilot/store_reader.py`.

### F11 · Timezone-correct relative dates
**Problem.** "What did I pick up today?" computed in UTC is wrong for most of the world for
part of every day.

**Solution.** `WORKSPACE_TIMEZONE` is a required, validated deployment variable with no
repository default, and date windows are resolved in that calendar.

**Where.** `agent/copilot/date_windows.py`, `infra/variables.tf`,
`test_date_windows_use_the_configured_local_calendar`.

### F12 · A conversation that survives Chat's interaction deadline
**Problem.** A copilot turn takes longer than the window Chat gives an app to answer, and a
timed-out interaction is a visible failure.

**Solution.** `weave-chat` republishes the untouched payload to `chat-events` and answers
immediately; ingestion runs the copilot against a session keyed on the space — so one DM
keeps its history across turns — and posts the reply into the DM. A copilot failure answers
with an apology rather than silence, and is never retried into a duplicate answer.

**Where.** `weave_chat/main.py::interact`, `main.py::chat_events`, `copilot_client.py`.

### F13 · Chat's own formatting dialect
**Problem.** Chat's plain-message syntax is not markdown, so a model's `**bold**` and `- `
bullets arrive as literal punctuation.

**Solution.** `to_chat_text` rewrites emphasis, headings and bullets into Chat's syntax,
holding code spans out so a literal `**` inside backticks stays content.

**Where.** `delivery/chat_text.py`.

---

## G. Safety and resilience

### G1 · Model Armor on the way in and out
**Problem.** Transcripts are untrusted input; model output reaching a user is untrusted
output.

**Solution.** Two templates. The input template screens transcripts for hate speech, dangerous
content, and prompt injection / jailbreak at HIGH confidence before any model sees them; a
blocked transcript is recorded observably and acked. The output template adds sensitive-data
protection on the copilot's responses.

**Where.** `infra/model_armor.tf`, `weave_ingestion/model_armor.py`, `agent/callbacks.py`.

### G2 · Untrusted-data discipline in every prompt
**Problem.** A transcript can contain "ignore your instructions". So can a document title, a
task, or a meeting excerpt.

**Solution.** Every prompt states that transcript text, tool results, meeting excerpts,
document titles and candidate text are **data, never instructions**. The architectural
backstop is that there are no write tools, so the strongest possible outcome of a crafted
transcript is a suggestion on a card a human reviews.

**Where.** all four prompt modules.

### G3 · No write path into work systems
**Problem.** Any write capability turns a prompt-injection bug into an incident.

**Solution.** There is no write tool anywhere in the agent tool surface — asserted by
enumerating the tool set. Weave never closes an external Google Task or writes to Drive. Its
writes are confined to its own derived state.

**Where.** `test_agent_tool_surface_is_read_only`.

### G4 · Blast-radius containment
**Problem.** One failure taking down a whole meeting loses everyone's work, not just the
failing part.

**Solution.** One owner's enrichment failure → one unenriched bundle. One delivery failure →
`delivered_partial` for that meeting, other owners unaffected. Reconciliation failure →
recorded on the meeting, delivery proceeds. Context source failure → no results from that
source. Embedding failure → lexical write. One user's subscription failure → that user's
record waits for the next sweep.

**Where.** `orchestrator.py`, `main.py::pubsub_push`, `registry.py::search_all`,
`subscription_manager/manager.py`.

### G5 · Atomic meeting persistence
**Problem.** A partial write leaves history that disagrees with the summary.

**Solution.** Action items and the meeting summary are written in one Firestore batch, with
the batch-size limit checked in advance. Persistence failure prevents delivery and retries
the meeting.

**Where.** `firestore_client.py::persist_meeting`.

### G6 · Fail-closed identity on the copilot
**Problem.** If a surface does not supply the caller's email, an empty answer reads as "you
have no work" — the most dangerous way for an identity failure to hide.

**Solution.** `principal.py` accepts an email-shaped ADK `user_id` and nothing else, and the
principal is overwritten every turn — a refusal writes an empty value rather than leaving the
previous turn's principal in place. There is deliberately **no** environment variable or
request field to override it for convenience.

**Where.** `agent/copilot/principal.py`, `test_refused_identity_neutralizes_the_previous_turns_principal`.

### G7 · Keyless domain-wide delegation
**Problem.** Exported service-account keys are the classic long-lived credential leak.

**Solution.** Service accounts sign their own DWD assertions through the IAM Credentials API
(`serviceAccountTokenCreator` on themselves). No key material exists.

**Where.** `infra/iam.tf`.

### G8 · Two hundred and sixty-five hermetic tests
**Problem.** Safety properties that are not tested are aspirations.

**Solution.** 265 tests across 29 files, with no network, no GCP credentials, and no LLM
calls — everything external sits behind an injectable seam. Every invariant in the
[security model](../engineering/security.md) is pinned by a named test. LLM-dependent
evaluation is separate (`make eval`) and never runs in CI.

**Where.** `tests/unit/`, `eval/`, `.github/workflows/ci.yaml`.

---

## H. Operability

### H1 · The whole pipeline runs with no cloud
**Problem.** A system testable only when fully deployed is slow to change and risky to
review.

**Solution.** `make demo` runs extraction, verification, per-owner enrichment and the
delivery contract over a bundled transcript on a developer's machine, with no GCP
dependencies at all.

**Where.** `scripts/demo.py`, `samples/`.

### H2 · Prompt and tool work against real data, without a redeploy
**Problem.** Iterating on a prompt through a full Agent Engine deployment is a slow loop.

**Solution.** `make web` serves both agents through `adk web` against the deployed Firestore.
The development UI supplies a fixed placeholder identity rather than an email, which the
copilot correctly refuses — call the server's API with an address to exercise it as a real
person.

**Where.** `Makefile::web`, `agent/copilot/principal.py`.

### H3 · Staged, reversible rollout switches
**Problem.** A single all-or-nothing switch makes every rollout an availability event.

**Solution.** Independent flags: `artifact_source` (fixture/live), `delivery_mode`
(log/chat), `create_cloud_run`, `create_subscription_manager`,
`manage_domain_restricted_sharing`, and `copilot_engine_id` — where an empty value is the
copilot rollback switch, leaving onboarding and delivery untouched.

**Where.** `infra/variables.tf`, `infra/deployed.auto.tfvars`.

### H4 · Stable agent endpoints across redeploys
**Problem.** The services that call Weave's agents hold a reference to them. If a redeploy
moved that reference, every caller would need reconfiguring.

**Solution.** `make deploy-agent` and `make deploy-copilot` update the existing deployment, so
a prompt change, model change, or dependency bump is invisible to everything pointing at it.

**Where.** `agent/deployment/`,
`test_a_recorded_engine_is_updated_in_place_never_recreated`.

### H5 · Configuration that fails at boot, not mid-meeting
**Problem.** A missing environment variable discovered during a meeting's processing costs
that meeting.

**Solution.** `Settings` is frozen and `extra="forbid"`, validated at app creation: a missing
variable raises at boot, an invalid timezone is rejected outright, and combinations that
cannot work (live reads or Chat delivery without an `admin_subject`) are refused before the
service serves traffic.

**Where.** `weave_ingestion/config.py`, `services/chat/weave_chat/config.py`.
