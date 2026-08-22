# A_PLAN — Weave Phase A: Local Foundation (+ B: Delivery Contract)

This plan covers everything buildable **without GCP** and (except two gated checks) **without an LLM**: the workspace skeleton, shared contracts, the extraction agent, the context-source framework, the orchestrator/enrichment pipeline, and the delivery contract. It is written for an implementing model: follow the steps in order, run every ✅ verification before moving on, and do not start a step until the previous step's checks pass.

The authoritative system description is the v2 build plan (see README / project docs). This file only expands steps **A1–A5 and B1** into concrete, verifiable work.

---

## 0. The four invariants, and which test owns each

These are product invariants enforced **in Python, never in a prompt**. Every one of them must be pinned by a named test in this phase. If you ever find yourself expressing one of these only in a prompt string, stop — you're doing it wrong.

| # | Invariant | Enforced by (code) | Pinned by (test) |
|---|---|---|---|
| 1 | Two-phase isolation: extraction sees the whole transcript but has **no context tools**; enrichment runs once per owner in a **fresh session** as that owner | agent construction (extraction agent's tool list), `run_pipeline` (new session per owner, only that owner's items in state) | `test_extraction_agent_has_no_context_tools`, `test_enrichment_session_state_contains_only_owner_items` |
| 2 | Assignment ≠ commitment: only `accepted` / `reassigned` are actionable; `accepted` requires `resolution_turn_ref`; silence is `unresolved` | `ACTIONABLE_STATUSES`, `ActionItem.is_actionable()`, pydantic validator | `test_commitment_filtering.py` |
| 3 | Identity from the Meet API, never from model output: `resolve_principal` fails closed (no email / confidence < 0.85 / not a real attendee → unenriched bundle, never a search) | `auth/principal_resolver.py`, orchestrator catch → unenriched bundle | `test_principal_resolver.py`, `test_transcript_only_attendee_is_refused_principal` |
| 4 | No mutation: no write tools anywhere in the agent's tool surface | tool modules (there is exactly one context tool, read-only), delivery stays outside the agent | `test_agent_tool_surface_is_read_only` (asserts the enumerated tool set) |

Plus one framework invariant from the v2 plan: **`SERVICE_ONLY` sources never serve a user query** unless explicitly allowed — pinned by `test_service_only_results_never_reach_the_caller`.

---

## 1. Engineering ground rules (apply to every step)

- **Python 3.12.** Change `.python-version` to `3.12`; root `pyproject.toml` sets `requires-python = ">=3.12,<3.13"`. (Agent Engine support for 3.13 is unverified; revisit at deploy time, not now.)
- **Pin dependencies deliberately.** `google-adk` pinned to an exact version (pick the latest 1.x at implementation time and record it); everything else with sensible lower bounds. One lockfile via `uv lock`, committed.
- **Unit tests are hermetic**: no network, no GCP credentials, no LLM calls. Anything that talks to a model or an API sits behind an injectable seam and is faked in tests.
- **Fail closed everywhere.** Unknown owner → `[]`. Missing principal → no search. Unknown source in config → refuse to register (raise at startup, not at query time). Defaults never widen access (`allow_service_only` defaults to `False`).
- **Types + lint.** Full type hints on public functions; `ruff check` and `ruff format --check` clean; keep modules small and single-purpose. Docstrings only where behaviour is non-obvious (validators, gates, the registry's drop semantics).
- **Pydantic models are the contract.** Cross-package data always crosses as `weave_common` schemas — never ad-hoc dicts. Prefer `model_config = ConfigDict(frozen=True)` where mutation isn't needed.
- **Don't build ahead.** No Jira/GitHub sources, no token broker, no write tools, no speculative config. The extension story is "one subclass + one config entry", documented, not scaffolded.
- After each step: `make lint && make test` must pass before proceeding.

---

## A0. Clear the `uv init` skeleton

The repo currently holds a default `uv init` app skeleton that conflicts with the target layout.

1. Delete `src/` (and the `[project.scripts]` entry pointing at it).
2. Rewrite `.python-version` → `3.12`.
3. `pyproject.toml` will be fully rewritten in A1 (workspace root, no `weave` package of its own).
4. Keep `.gitignore` and `README.md`; extend `.gitignore` in A1.

✅ **Check:** `git status` shows `src/` gone; no file anywhere references `src.weave` or `weave:main`.

---

## A1. Workspace skeleton

### Target layout

```
weave/
  pyproject.toml            # uv workspace root (no code of its own)
  uv.lock
  .python-version           # 3.12
  Makefile
  .env.example
  .gitignore
  .github/workflows/ci.yaml
  shared/
    pyproject.toml          # package: weave-common
    weave_common/
      __init__.py
      schemas.py
  agent/
    pyproject.toml          # package: weave-agent
    __init__.py             # agent/ IS the python package `agent` (ADK convention)
    agent.py                # lazy root_agent
    agents/__init__.py
    tools/__init__.py
    context_sources/__init__.py
    context_sources/sources/__init__.py
    auth/__init__.py
    prompts/__init__.py
    callbacks.py
    deployment/             # empty until D2 (keep a .gitkeep, no stub code)
  services/ingestion/
    pyproject.toml          # package: weave-ingestion
    weave_ingestion/
      __init__.py
      delivery/__init__.py  # filled in B1; FastAPI app etc. arrive in D1
  eval/                     # adk eval sets (filled in A3/A5)
  samples/                  # demo transcripts (filled in A5)
  tests/
    conftest.py
    unit/
```

Layout notes (deliberate decisions — keep them):

- `agent/` is **both** a workspace member and the importable package `agent`, so `adk web .` run from the repo root discovers it (ADK looks for subdirectories whose `__init__.py` exposes `root_agent`). Use the uv build backend's `module-root = ""` / `module-name` settings (or hatchling equivalent) so the co-located `pyproject.toml` builds the in-place package. Same technique for `shared/` → module `weave_common`.
- Ingestion code lives in a proper package `weave_ingestion` (slight refinement of the v2 sketch's flat files) so `from weave_ingestion.delivery.base import Deliverer` works both in tests and in the eventual container.

### Dependencies

- **root** (workspace): `dependency-groups.dev = [pytest, pytest-asyncio, ruff]`; workspace members wired via `[tool.uv.workspace]` + `[tool.uv.sources]`.
- **weave-common**: `pydantic` only. Nothing else, ever — this package must stay importable everywhere (including inside Agent Engine) with zero heavy deps.
- **weave-agent**: `weave-common`, `google-adk==<pinned>`, `pyyaml`, `google-cloud-firestore` (needed by `prior_meeting_source`; unit tests fake the client).
- **weave-ingestion**: `weave-common`, and for this phase only what B1 needs: `google-api-python-client`, `google-auth`. (fastapi/uvicorn/model-armor/aiplatform arrive with D1 — do not add dead deps now.)

### Makefile

Targets (each a thin `uv run` wrapper):

```
install   uv sync --all-packages
lint      ruff check . && ruff format --check .
test      pytest tests/unit -q
eval      guarded: fail with a clear message if no GOOGLE_API_KEY/Vertex ADC; else adk eval (A3/A5 sets)
web       adk web .            # local agent playground
demo      python scripts/demo.py --transcript $(TRANSCRIPT)   # written in A5
```

`.env.example`: `GOOGLE_API_KEY=` plus commented Vertex alternative (`GOOGLE_GENAI_USE_VERTEXAI=1`, `GOOGLE_CLOUD_PROJECT=`, `GOOGLE_CLOUD_LOCATION=`). `.gitignore` additions: `.env`, `*.tfstate*`, `.adk/`, `.venv/`, `__pycache__/`.

### CI (`.github/workflows/ci.yaml`)

One job: checkout → install uv → `make install` → `make lint` → `make test`. **CI never runs `make eval`** (needs a key). Python 3.12 only.

### tests/conftest.py

If the editable workspace install makes `weave_common`, `agent`, and `weave_ingestion` importable (it should), conftest needs no path hacks — verify with a trivial import test rather than adding `sys.path` surgery preemptively. Add path inserts only if `adk`'s own loading conventions force them, and comment why.

✅ **Check (A1):** on a fresh clone, `make install && make lint && make test` succeeds (test suite may be a single placeholder import test at this point: imports `weave_common`, `agent`, `weave_ingestion`). `uv run python -c "import weave_common, agent, weave_ingestion"` exits 0.

---

## A2. Shared contracts — `shared/weave_common/schemas.py`

Everything here is pure pydantic. No ADK imports, no Google imports.

### Enums

- `CommitmentStatus`: `accepted`, `declined`, `deferred`, `reassigned`, `unresolved`. Docstring: *silence is `unresolved`* — extraction must never promote silence to acceptance.
- `ACTIONABLE_STATUSES: frozenset[CommitmentStatus] = frozenset({ACCEPTED, REASSIGNED})` — module-level constant. **The only definition in the codebase**; everything else imports it (A5's `redaction.py` in particular).
- `ActionType`: start small — `task`, `follow_up`, `decision_needed`. Extend only when an eval case demands it.
- `MatchType`: `existing_prior_item`, `related_discussion`, `none`.

### Models (fields beyond the obvious)

- `Attendee{email: str, participant_id: str, display_name: str}`
- `TranscriptTurn{turn_index: int, participant_id: str | None, speaker_name: str, text: str}`
- `ActionItem`:
  - `description`, `action_type`, `status: CommitmentStatus`
  - `owner_email: str | None` — None when unresolved; **never invented**
  - `owner_confidence: float` (0–1) — comes from the `resolve_speaker` tool, threaded through; there is **no default of 1.0** (make it a required field so a forgotten value is a validation error, not a silently-open gate)
  - `commitment_turn_ref: int | None` — turn where the item was raised
  - `resolution_turn_ref: int | None` — turn where it was accepted/declined/reassigned
  - `deadline: date | None`, `deadline_source_text: str | None`
  - **model validator**: `status == accepted` ⇒ `resolution_turn_ref is not None`, else `ValidationError`. This is invariant 2's hard edge.
  - `is_actionable() -> bool`: `self.status in ACTIONABLE_STATUSES`
- `MeetingInsights{conference_record_id, meeting_date, items: list[ActionItem]}` with `items_for_owner(email) -> list[ActionItem]`: actionable items whose `owner_email` equals `email` (case-insensitive); unknown owner → `[]` (fail closed, no raise).
- `ContextMatch{source_name, match_type: MatchType, title, snippet, ref: str | None, score: float | None}`
- `EnrichedActionItem`: `item: ActionItem` + `matches: list[ContextMatch]` (empty list = "no context found", still valid).
- `OwnerItemList{owner_email, items: list[EnrichedActionItem]}` — the enrichment agent's `output_schema`.
- `EnrichedOwnerBundle{owner_email, items, enriched: bool, skip_reason: str | None}` — what the pipeline returns per owner; `enriched=False` + `skip_reason` is the "ship unenriched" path.
- `PipelineRequest{transcript_turns: list[TranscriptTurn], conference_record_id, meeting_date, attendees: list[Attendee]}`
- `PipelineResult{conference_record_id, bundles: list[EnrichedOwnerBundle], dropped_item_count: int}`

✅ **Check (A2):** `tests/unit/test_commitment_filtering.py` passes, covering at minimum:
- declined / deferred / unresolved items are not actionable; accepted / reassigned are;
- constructing an `accepted` item without `resolution_turn_ref` raises `ValidationError`;
- `items_for_owner` on an email not present returns `[]` and is case-insensitive;
- `items_for_owner` never returns non-actionable items even when the owner matches;
- `ActionItem` cannot be constructed without an explicit `owner_confidence`.

---

## A3. Extraction agent

### Tools (deterministic, fully unit-tested offline)

`agent/tools/speaker_resolution_tool.py` — `resolve_speaker(speaker: str, tool_context) -> dict`:
- Reads `attendees: list[Attendee]` from `tool_context.state["attendees"]` (placed there by the orchestrator / demo harness — **never** from model output).
- Exact `participant_id` match → `{email, confidence: 1.0, method: "participant_id"}`.
- Else exact case-insensitive `display_name` match, unique → confidence `0.95`.
- Else `difflib.SequenceMatcher` against display names **and** first names (handles "Sarah" vs "Sarah Chen"): best ratio scaled into (0, 0.9]; if the top two candidates are within 0.05 of each other → ambiguous → `{email: None, confidence: 0.0, method: "ambiguous"}`.
- No attendees in state → `{email: None, confidence: 0.0, method: "no_attendees"}`. Never raises.

`agent/tools/deadline_inference_tool.py` — `infer_deadline(phrase: str, meeting_date: date) -> date | None`:
- Pure function + thin tool wrapper. Handles: ISO dates, weekday names ("Friday" → next occurrence strictly after `meeting_date`), "tomorrow", "end of (the) week/day/month", "next week" (→ Monday after next weekend), "in N days/weeks".
- Anything it can't parse → `None`. It must **never guess**: no "probably next month".

### Prompts — `agent/prompts/`

`extraction_prompt.py` (a string constant, not a template engine). Content requirements:
- Extract candidate action items with the **turn refs** for both assignment and resolution.
- Call `resolve_speaker` for every owner attribution; copy the returned email/confidence verbatim into the output — instruct the model that inventing emails or confidences is a hard error.
- Call `infer_deadline` for spoken deadlines; no tool result → `deadline: null`.
- Status semantics spelled out with one example each: explicit yes → `accepted` (with the resolution turn); explicit no → `declined`; "let's revisit" → `deferred`; assigned to someone else who accepts → `reassigned`; **no response → `unresolved`**.
- The prompt is *guidance*; remember every rule stated here is *also* enforced structurally (validator, gates). Never rely on the prompt alone.

### Agent — `agent/agents/extraction.py`

- `LlmAgent`, model `gemini-2.5-flash`, tools = the two above, aiming for `output_schema=MeetingInsights`.
- ⚠️ **Known ADK constraint:** many ADK versions disallow `output_schema` together with `tools` on one agent. Check the pinned version first. If disallowed, use the standard split: `extraction_worker` (tools, no schema, instructed to emit final JSON) → wrap with an `after_model_callback` (or a second schema-only formatter agent) that parses/validates the text into `MeetingInsights` via `model_validate_json`, retry-once on parse failure. Whichever shape you use, **the public surface is one callable that returns a validated `MeetingInsights`** — the orchestrator must not care.
- Extraction has **no context tools** (invariant 1) and no write tools (invariant 4). Add `tests/unit/test_extraction_agent_has_no_context_tools.py`: asserts the extraction agent's tool names are exactly `{resolve_speaker, infer_deadline}`; and a companion `test_agent_tool_surface_is_read_only` asserting the full set of tools defined anywhere under `agent/tools/` is exactly the three known read-only tools (two above + `search_related_context` from A4 — update the set there).

### Lazy `root_agent` — `agent/agent.py`

`adk web` / `adk eval` import `agent.root_agent`. Expose it via module `__getattr__` so that importing `agent` (e.g. from tests that only want tools) does not construct an `LlmAgent` or require credentials. `root_agent` = the extraction agent only (the orchestrator is not an ADK agent and is deployed separately in D2).

### Eval set — `eval/extraction_cases.json`

Cases (ADK evalset format for the pinned version — check `adk eval --help` / the version's docs, the format has changed between releases):
1. clear accept with spoken deadline → `accepted`, correct `resolution_turn_ref`, deadline resolved;
2. explicit decline → `declined`, not actionable;
3. assignment met with **silence** → `unresolved`;
4. deferral ("let's revisit next sprint") → `deferred`;
5. reassignment accepted by the new owner → `reassigned` with new owner's email;
6. accept with no deadline → `deadline: null` (model must not invent one);
7. ambiguous owner (two attendees named Alex) → low/zero confidence, owner unresolved.

✅ **Check (A3):**
- Offline: all tool unit tests pass (`test_speaker_resolution.py` — id match, name match, first-name fuzzy, ambiguous tie, empty attendees; `test_deadline_inference.py` — table-driven over the phrase list incl. year-boundary weekday); tool-surface tests pass.
- Gated (needs `GOOGLE_API_KEY` or Vertex ADC): `make eval` runs the extraction set and passes. If no key is available in the environment, `make eval` exits with a clear "set GOOGLE_API_KEY" message — that is the expected outcome for this check in a keyless environment; record it in the PR/commit message and move on.

---

## A4. Context-source framework

### `agent/context_sources/base.py`

- `AuthMode` enum: `USER_CONTEXT` (search executes constrained to a specific principal), `SERVICE_ONLY` (source cannot scope to a user — dangerous by default).
- `SearchPrincipal{email: str, credential_ref: str | None}` (frozen).
- `ContextSource` ABC: `name: str`, `auth_mode: AuthMode`, `search(query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]`.

### `agent/context_sources/registry.py`

- `@register_source(name, auth_mode)` class decorator → global registry dict.
- `build_sources(config: dict, *, allow_service_only: bool = False) -> list[ContextSource]`:
  - config names an unregistered source → **raise at build time** (startup failure is the safe failure);
  - registered but `SERVICE_ONLY` and not allowed → **drop with a warning log**, never instantiate;
- `search_all(sources, query, principal) -> list[ContextMatch]`: per-source try/except — a source that raises is logged and skipped; **the caller never sees an exception** (a broken source must not kill an enrichment run).

### `agent/context_sources/config.yaml`

```yaml
sources:
  - name: prior_meetings
    enabled: true
```
Loader validates against registered names. No dead entries for future sources.

### `agent/context_sources/sources/prior_meeting_source.py`

- `@register_source("prior_meetings", AuthMode.USER_CONTEXT)`.
- Constructor takes an injectable Firestore-like client (default: real `google.cloud.firestore.Client` constructed lazily on first search, so importing the module never needs credentials).
- Query: collection `action_items`, `where visible_to array_contains principal.email`, `order_by created_at desc`, `limit N` → mapped to `ContextMatch(match_type=existing_prior_item …)`. **The principal filter lives in the query** — never fetch-then-filter.
- Tests use a small in-memory fake implementing just the query-chain surface used (`collection().where().order_by().limit().stream()`); put the fake in `tests/unit/fakes.py` — it is reused by A5's demo.

### The single tool — `agent/tools/search_related_context_tool.py`

`search_related_context(query: str, tool_context) -> list[dict]`:
- Reads `SearchPrincipal` from `tool_context.state["search_principal"]`. **Missing principal → return `[]`** (and log) — the tool never searches unscoped and never raises to the model.
- Delegates to `registry.search_all` over the sources built from config; returns `ContextMatch.model_dump()` dicts.
- This is the **only** context tool; new sources plug in behind it, the tool surface never grows per-source.

✅ **Check (A4):** `tests/unit/test_context_source_registry.py` passes, including at minimum:
- `test_service_only_results_never_reach_the_caller` — register a fake `SERVICE_ONLY` source that would return a marker string; build with defaults; assert the marker is unreachable via `search_all` **and** via the tool;
- unknown source name in config raises at build;
- a source raising mid-search is skipped, others still return;
- tool with no principal in state returns `[]`;
- prior-meeting source with the in-memory fake: only docs whose `visible_to` contains the principal's email come back, newest first.

---

## A5. Orchestrator + enrichment

### `agent/auth/principal_resolver.py`

`resolve_principal(owner_email: str | None, confidence: float, attendee_emails: set[str]) -> SearchPrincipal`:
- Raises `PrincipalResolutionError(reason)` when: `owner_email` is falsy; `confidence < 0.85`; `owner_email.lower()` not in the (lowercased) attendee set. Reasons are machine-readable (`"no_email" | "low_confidence" | "not_attendee"`) — they become `skip_reason` and later a metric.
- No other code path may construct a `SearchPrincipal` for enrichment.

### `agent/auth/redaction.py`

Helpers to strip non-actionable items and foreign-owner items from anything headed into an enrichment context. **Imports `ACTIONABLE_STATUSES` from `weave_common` — never redefines it** (add a lint-style unit test asserting `redaction` has no constant of that name).

### Enrichment agent — `agent/agents/enrichment.py`

- `LlmAgent`, `gemini-2.5-flash`, single tool `search_related_context`, `output_schema=OwnerItemList` (same ADK tools+schema caveat as A3 — reuse whatever shape A3 landed on).
- Prompt: you are enriching **one owner's** items with related prior context; call the tool per item with a focused query; unmatched item → empty `matches`; never add, drop, or reword items.
- `before_agent_callback=log_enrichment_scope` (in `callbacks.py`): logs owner email + item count + conference id — the observability hook the v2 plan requires (Model Armor output callback is added at D2, not now).

### Orchestrator — `agent/agents/orchestrator.py`

`run_pipeline(req: PipelineRequest) -> PipelineResult` — **plain Python, not an LlmAgent**. For testability, the two model touchpoints are injectable:

```python
def run_pipeline(
    req: PipelineRequest,
    *,
    extract: Callable[[PipelineRequest], MeetingInsights] = run_extraction,
    enrich: Callable[[SearchPrincipal, list[ActionItem]], OwnerItemList] = run_enrichment,
) -> PipelineResult:
```

Flow:
1. `insights = extract(req)` — default impl runs the extraction agent via an ADK runner with `attendees` seeded into session state.
2. Filter to actionable items (`is_actionable()`); count the dropped.
3. Group by `owner_email`.
4. Per owner: `resolve_principal(owner_email, min(item.owner_confidence for items), attendee_emails_from_req)`.
   - On `PrincipalResolutionError` → `EnrichedOwnerBundle(enriched=False, skip_reason=…, items=<unenriched wraps>)`. **Ship unenriched, never search** — the items still reach delivery, only context lookup is refused.
   - On success → `enrich(principal, owner_items)` — default impl: **fresh `InMemoryRunner` session per owner**, state = `{search_principal, owner_items}` and *only* that owner's items; parse to `OwnerItemList`.
5. `enforce_owner_scope(owner_email, owner_items, result)` — pure function, defense-in-depth: drop (and log) any returned item whose identity isn't in the input set for that owner; the enrichment model can annotate items, never mint or import them.
6. Return `PipelineResult` with one bundle per owner.

Any per-owner enrichment exception (not just principal errors) degrades to an unenriched bundle for that owner — one owner's failure never sinks the meeting.

### Demo — `scripts/demo.py` + `samples/standup.txt`

- `samples/standup.txt`: simple `Name: utterance` lines covering accept/decline/silence/reassign, plus a tiny attendees sidecar (`samples/standup.attendees.json`) with emails + participant ids — the demo must not parse identity out of the transcript text (that's the production Meet API's job).
- `scripts/demo.py`: build `PipelineRequest` from the sample, wire `prior_meetings` to the in-memory fake pre-loaded with one prior item visible to one attendee, call `run_pipeline` (real agents — needs a key), pretty-print each `EnrichedOwnerBundle` separately with a clear per-owner divider.

### Eval set — `eval/leakage_cases.json`

Cases probing cross-owner leakage at the model level: e.g. owner A's prior context contains a distinctive marker; assert owner B's enrichment output never contains it; a transcript-only name is never enriched. (These complement — never replace — the structural unit tests.)

✅ **Check (A5):**
- Offline (`make test`), with stub `extract` / `enrich`:
  - `test_transcript_only_attendee_is_refused_principal` — an owner email appearing in transcript text but absent from `req.attendees` → bundle has `enriched=False, skip_reason="not_attendee"`;
  - low confidence (0.84) → skipped; 0.85 → passes;
  - `test_enrichment_session_state_contains_only_owner_items` — capture the state handed to `enrich` per owner; assert no foreign items;
  - `enforce_owner_scope` drops a smuggled foreign item and keeps legitimate ones;
  - a stub `enrich` that raises → that owner degrades to unenriched, other owners unaffected;
  - non-actionable items never reach grouping; `dropped_item_count` is right.
- Gated (needs key): leakage evals pass; `make demo TRANSCRIPT=samples/standup.txt` prints separate per-owner bundles, the declined and silent items appear in no bundle, and the owner with a prior item shows an `existing_prior_item` match.

---

## B1. Delivery contract (local)

### `services/ingestion/weave_ingestion/delivery/base.py`

- `Deliverer` ABC: `deliver(owner_email: str, bundle: EnrichedOwnerBundle) -> str` (returns a delivery id).
- `build_card(bundle) -> dict` — **module-level pure function** (not a private method: it is the shared contract every deliverer renders from, and tests hit it directly). Produces a Google Chat **Card v2** dict:
  - title: meeting-scoped ("Your action items from <meeting/date>");
  - one section per item: description, status badge, commitment turn ref, deadline (omit row if None), matched context (source + title per match);
  - `enriched=False` or empty matches → an explicit "no related context found" line — silence must be distinguishable from failure;
  - never includes another owner's email or items (by construction: input is one bundle).

### `services/ingestion/weave_ingestion/delivery/chat.py`

`ChatDeliverer(Deliverer)`: Google Chat API, app auth, DM to `owner_email`, sends `build_card` output. The Chat API client is injected (constructor arg); unit tests use a recording fake and assert the space-lookup + message payload; **no real Chat call in this phase** (the live smoke is C5's `make chat-smoke`).

### `services/ingestion/weave_ingestion/delivery/gemini_enterprise.py`

`GeminiEnterpriseDeliverer(Deliverer)`: constructor or `deliver` raises `NotImplementedError("GE Inbox delivery unverified — see build plan §F")`. No speculative code.

✅ **Check (B1):** `tests/unit/test_delivery.py` passes:
- `build_card` on a full sample bundle yields valid Card v2 structure (assert key paths, item count, turn refs present);
- unenriched bundle → card contains the "no related context found" text;
- deadline-less item renders without a deadline row;
- `ChatDeliverer` with the fake client sends exactly one message to the owner's DM with the built card;
- `GeminiEnterpriseDeliverer` raises `NotImplementedError`.

Also: `make demo` now additionally prints the rendered card (as formatted JSON) under each bundle.

---

## Final acceptance checklist for this phase

Run on a fresh clone, in order:

1. `make install` — clean sync, lockfile respected, Python 3.12.
2. `make lint` — zero findings.
3. `make test` — all unit tests green, **no network, no credentials, no LLM**. Verify hermeticity by running once with `GOOGLE_API_KEY` unset and no ADC available.
4. Invariant audit — each row of the table in §0 names a test; confirm each test exists and fails if you break its invariant (spot-check by temporary mutation, e.g. set the confidence gate to `> 0.0` and watch `test_principal_resolver` fail).
5. `grep -rn "ACTIONABLE_STATUSES" --include='*.py' | grep -v weave_common/schemas.py | grep -v test` — every hit is an import, none a definition.
6. Repo hygiene: no `src/`, no empty stub modules "for later" (except `deployment/.gitkeep`), no unused deps in any `pyproject.toml`, README updated with a 5-line "what is here / how to run tests" section.
7. With a key available: `make eval` (extraction + leakage) green; `make demo TRANSCRIPT=samples/standup.txt` shows per-owner bundles + cards, no cross-owner content.
8. CI workflow runs steps 1–3 and is green.

**Done means:** checklist items 1–6 and 8 pass unconditionally; item 7 passes wherever a key exists, and its gating message is clean where one doesn't.
