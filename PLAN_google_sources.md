# PLAN_google_sources.md — Google Docs/Drive and Google Tasks as per-user context sources

## Goal

Give the enrichment agent two new owner-scoped context sources — **Google Docs/Drive**
(documents the owner can see) and **Google Tasks** (the owner's own open tasks) — so
`search_related_context` can surface personal work context beyond prior meeting action
items. Every search must run *as the owner being enriched* (per-user ACLs, not a shared
corpus), and the existing security invariants must survive unchanged.

### Explicitly out of scope

- **Google Issue Tracker (Buganizer): excluded — it has no public API.**
  `issuetracker.google.com` is web-UI only for external users; there is no documented
  REST surface, no OAuth scope, and no DWD support. Do not attempt the undocumented
  internal endpoints the web UI uses. Recorded in README "Future scope".
- **External OAuth connectors (Jira, Confluence, …): deferred.** The design (one
  `ContextSource` per provider, per-user OAuth refresh tokens in Secret Manager keyed
  per service, `SearchPrincipal.credential_ref` carrying the secret name) is recorded
  in README "Future scope". Nothing in this plan may preclude it.

## The one architectural decision: a context broker, not agent-side DWD

`infra/iam.tf` declares the security model in one line:

```
display_name = "Weave ingestion service (only SA with domain-wide delegation)"
display_name = "Weave Agent Engine runtime (no delegation, ever)"
```

The agent runs LLM-directed tool calls; giving its SA domain-wide impersonation would
let a prompt-injected tool call read any user's Drive. **That invariant is preserved:
the agent SA gets no DWD.** Instead:

- The ingestion Cloud Run service (which already signs DWD assertions via
  `google_auth.delegated_credentials`) grows one new authenticated route,
  **`POST /context/search`** — the *context broker*.
- The agent-side sources (`google_docs`, `google_tasks`) are thin HTTP proxies: they
  POST `{source, query, principal_email, limit}` to the broker with an OIDC ID token
  and parse `ContextMatch` JSON back.
- The broker — deterministic, non-LLM code — is the only place that impersonates, and
  it enforces every constraint server-side: caller identity, source allowlist,
  subject-must-be-onboarded, fixed read-only scopes, limit clamp, query escaping.

Failure anywhere degrades to zero matches for that source (the registry's
`search_all` already isolates source failures), never to a pipeline failure.

### Security properties (the checklist a reviewer verifies at the end)

1. Agent SA has no DWD grant, before and after. Only `roles/run.invoker` on the
   ingestion service is added.
2. The broker rejects any caller whose verified OIDC identity is not exactly the
   agent SA (`weave-agent-sa@<project>.iam.gserviceaccount.com`).
3. The broker impersonates only `principal_email`, and only when that email belongs
   to a currently **onboarded** user (Firestore check, fail-closed). A non-onboarded
   meeting attendee silently gets no Google-source matches — that is correct behavior.
4. Scopes are hardcoded server-side to `drive.readonly` and `tasks.readonly`; the
   request body cannot influence them.
5. Model-generated query text is escaped before entering the Drive `q` string
   (backslash and single-quote), and length-capped.
6. Every delegated search is logged with subject, source, and result count (not the
   raw query — it may contain meeting content).

---

## Step 0 — shared contracts (`shared/weave_common/`)

**`shared/weave_common/schemas.py`**

1. Add two members to `MatchType` (safe: `StrEnum`, additive, both sides rebuilt from
   the same wheel):

```python
class MatchType(StrEnum):
    EXISTING_PRIOR_ITEM = "existing_prior_item"
    RELATED_DISCUSSION = "related_discussion"
    RELATED_DOCUMENT = "related_document"
    OPEN_TASK = "open_task"
```

No `ContextMatch` field changes: `title`, `snippet`, `ref` (URL for docs, task id for
tasks), optional `score`, `occurred_on` already carry everything the enrichment
prompt needs.

2. **Move the lexical ranker into the shared package.** The broker (ingestion side)
needs `rank()` for Tasks (the Tasks API has no server-side search), and it currently
lives in `agent/context_sources/relevance.py`. Create
`shared/weave_common/relevance.py` with the exact current implementation, export
`rank` from `weave_common.__init__`, and turn `agent/context_sources/relevance.py`
into a re-export (`from weave_common.relevance import rank`) so
`prior_meeting_source.py` and `tests/unit/test_relevance.py` keep working untouched.

## Step 1 — the context broker (ingestion service)

**New file `services/ingestion/weave_ingestion/google_sources.py`** — all search
logic, injectable for tests:

```python
"""Delegated Google-source searches for the context broker route."""

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
# DRIVE_SCOPE already exists in main.py; move both scope constants here.

MAX_LIMIT = 20          # broker-side clamp, matches agent-side RECALL
MAX_QUERY_CHARS = 400
TASK_CANDIDATES = 100   # per task list, before lexical ranking


def _escape_drive_query(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


class GoogleSourceBroker:
    def __init__(self, build_drive_service, build_tasks_service) -> None: ...
    def search(self, source: str, query: str, subject: str, limit: int) -> list[ContextMatch]:
        # dispatch on source; raise ValueError for unknown source names
```

- **`_search_docs(subject, query, limit)`** — Drive `files.list` as the subject:
  `q=f"fullText contains '{_escape_drive_query(query)}' and trashed=false"`,
  `corpora="user"`, `orderBy="modifiedTime desc"`, `pageSize=limit`,
  `fields="files(id,name,mimeType,modifiedTime,webViewLink)"`. Map each file to
  `ContextMatch(source_name="google_docs", match_type=RELATED_DOCUMENT,
  title=<file name>, snippet=f"{mimeType} last modified {modifiedTime[:10]}",
  ref=<webViewLink>, score=None, occurred_on=<modifiedTime date>)`. Drive returns no
  content snippet; the enrichment agent judges from title + recency, which is exactly
  the judgment layer's job. `score=None` is deliberate — do not invent one.
- **`_search_tasks(subject, query, limit)`** — `tasklists().list()`, then per list
  `tasks().list(tasklist=id, showCompleted=False, showHidden=False,
  maxResults=TASK_CANDIDATES)`. Rank `f"{title}\n{notes}"` with `weave_common.rank`
  against the query, take top `limit`, map to
  `ContextMatch(source_name="google_tasks", match_type=OPEN_TASK, title=<task title>,
  snippet=<notes or title>, ref=<task selfLink or id>, score=<round(rank score, 4)>,
  occurred_on=<due date if set, else updated date>)`.

**`services/ingestion/weave_ingestion/oidc.py`** — add a second verifier (do not
touch `verify_push_token`):

```python
def verify_caller_token(token: str, *, audience: str, expected_sa: str) -> dict[str, Any]:
    # identical structure to verify_push_token; separate function so the push
    # path and the broker path can never drift into sharing an identity check
```

**`services/ingestion/weave_ingestion/main.py`**

- `_agent_sa(settings)` helper mirroring `_ingestion_sa`:
  `f"weave-agent-sa@{settings.project_id}.iam.gserviceaccount.com"`.
- Build the broker in `create_app` (injectable param `broker=None` like the others),
  using `delegated_credentials` closures identical in shape to `build_drive_service`
  — the Tasks closure uses `build("tasks", "v1", ...)`.
- New route:

```python
@app.post("/context/search")
async def context_search(request: Request) -> Response:
    # 1. Bearer token → verify_caller_token(audience=settings.pubsub_push_audience,
    #    expected_sa=_agent_sa(settings)); 403 on any failure, no detail.
    # 2. Parse body {source, query, principal_email, limit} via a small pydantic
    #    model (extra="forbid"); 400 on validation error.
    # 3. subject = principal_email.strip().casefold(); require subject in
    #    ledger.onboarded_by_email() — else return {"matches": []} with a
    #    logger.info("broker refused non-onboarded subject", ...). Fail closed,
    #    but 200: an empty result, not an error the registry would log as failure.
    # 4. limit = max(1, min(limit, MAX_LIMIT)); len(query) <= MAX_QUERY_CHARS or truncate.
    # 5. matches = broker.search(source, query, subject, limit)  # ValueError → 400
    # 6. Return {"matches": [m.model_dump(mode="json") for m in matches]};
    #    log subject, source, count.
```

Reusing `settings.pubsub_push_audience` as the OIDC audience is intentional: it is
already registered in `custom_audiences` on the Cloud Run service, so no Terraform
audience change is needed; caller identity is what separates the routes.

## Step 2 — agent-side proxy sources

**New file `agent/context_sources/broker_client.py`**:

```python
"""Authenticated client for the ingestion context broker."""

import google.auth.transport.requests
from google.oauth2 import id_token

TIMEOUT_SECONDS = 8  # a slow source must not stall enrichment; registry eats the failure


def fetch_broker_matches(base_url, audience, source, query, principal_email, limit):
    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
    # POST f"{base_url}/context/search" with Authorization: Bearer <token>,
    # json={...}, timeout=TIMEOUT_SECONDS; raise for non-200;
    # return [ContextMatch.model_validate(m) for m in payload["matches"]]
```

Use `requests` (already a transitive dependency via google-auth transport; if the
Agent Engine wheel build complains, add `requests` to `REQUIREMENTS` explicitly).
`fetch_id_token` reads the metadata identity endpoint; **verify it works on Agent
Engine during rollout step V2** — if the runtime does not expose an identity token
endpoint, the fallback is the IAM Credentials `generateIdToken` API on the agent's
own SA, which requires adding `agent_self_signer`
(`roles/iam.serviceAccountTokenCreator` on itself, signing only — still zero
delegation). Do not add that binding unless the fallback is actually needed.

**New file `agent/context_sources/sources/google_docs_source.py`** (and
`google_tasks_source.py`, identical shape):

```python
@register_source("google_docs", AuthMode.USER_CONTEXT)
class GoogleDocsSource(ContextSource):
    def __init__(
        self, base_url: str | None = None, audience: str | None = None, fetch_fn=None
    ) -> None:
        self._base_url = base_url or os.environ.get("CONTEXT_BROKER_URL", "")
        self._audience = audience or os.environ.get("CONTEXT_BROKER_AUDIENCE", "")
        self._fetch = fetch_fn or fetch_broker_matches

    def search(self, query, principal, limit=5):
        if not self._base_url or not self._audience:
            logger.warning("google_docs source disabled: broker not configured")
            return []
        if not query.strip() or limit <= 0:
            return []
        return self._fetch(
            self._base_url, self._audience, "google_docs", query, principal.email, limit
        )
```

- Import both modules from `agent/context_sources/sources/__init__.py` (that is what
  registers them; mirror how `prior_meeting_source` is imported).
- **`agent/context_sources/config.yaml`**:

```yaml
sources:
  - name: prior_meetings
    enabled: true
  - name: google_docs
    enabled: true
  - name: google_tasks
    enabled: true
```

- **`agent/deployment/deploy.py`** — two new env vars in `env_vars`, read from the
  deploy environment (`make deploy-agent` must export them from
  `tofu output -raw ingestion_url` and the `pubsub_push_audience` tfvar):

```python
"CONTEXT_BROKER_URL": os.environ.get("CONTEXT_BROKER_URL", ""),
"CONTEXT_BROKER_AUDIENCE": os.environ.get("CONTEXT_BROKER_AUDIENCE", ""),
```

Empty values must not crash `set_up` — the sources log-and-return-[] (see guard
above), so the agent still works with prior_meetings only. Update the `Makefile`
`deploy-agent` target to export both (fail the target loudly if `ingestion_url` is
empty so a half-configured deploy is impossible to miss).

## Step 3 — enrichment prompt

`agent/prompts/enrichment_prompt.py`: extend the candidate-judgment section with one
paragraph: candidates now also carry `match_type` values `related_document` (a Drive
file the owner can open; `ref` is its link; there is no content snippet, so judge by
title and recency) and `open_task` (one of the owner's own open Google Tasks).
Keep-or-reject rules are unchanged: relevance to *this* action item decides, staleness
via `occurred_on` vs the current meeting date, and rejected candidates must not leak
into `details`. A kept document's link may be mentioned in `details` by name, never
as a raw dump of the match list.

## Step 4 — infrastructure and manual steps

1. **`infra/apis.tf`** — add `"tasks.googleapis.com"` to `local.services`. (Lesson
   from the Drive rollout: the DWD scope grant and the API enablement are separate
   switches; both are required.)
2. **`infra/iam.tf`** — the agent may invoke the broker:

```hcl
# The agent calls the ingestion context broker; identity is re-verified in-app.
resource "google_cloud_run_v2_service_iam_member" "agent_invoker" {
  count    = var.create_cloud_run ? 1 : 0
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.agent.member
}
```

   (Place it in `cloud_run.tf` next to `push_invoker` if the `count` guard reads
   better there.) **No other IAM change. Nothing touches the agent SA's roles.**
3. **`infra/SETUP.md` §5** — extend the ingestion SA row's scope list with
   `https://www.googleapis.com/auth/tasks.readonly`.
4. **Manual (Workspace Admin Console)** — edit the existing
   `ingestion_sa_unique_id` DWD entry at
   https://admin.google.com/ac/owl/domainwidedelegation and re-save it with the
   Tasks scope appended. Editing an entry replaces its scope list, so paste the
   full four-scope line. Propagation takes minutes; `403 unauthorized_client`
   from the Tasks API afterwards means wait, not a code bug.

## Step 5 — tests (all hermetic; extend existing files where named)

**New `tests/unit/test_google_sources.py`** (broker logic):
- Drive: query with `'` and `\` is escaped in the generated `q`; `trashed=false`
  always present; file rows map to `RELATED_DOCUMENT` matches with
  `occurred_on == modifiedTime date` and `score is None`.
- Tasks: candidates ranked lexically; completed tasks never requested
  (`showCompleted=False` asserted on the fake); `occurred_on` prefers due date.
- Unknown source name raises `ValueError`.

**Extend `tests/unit/test_ingestion_app.py`** (or the existing main-route test file):
- `/context/search` without a bearer token → 403; with a token the verifier rejects
  → 403; verifier asserting wrong SA email → 403.
- Valid caller, `principal_email` not onboarded → 200 with `{"matches": []}` and
  the broker fake never called.
- Valid caller + onboarded subject → broker fake called with clamped limit
  (`limit=999` in → `MAX_LIMIT` at the fake) and casefolded subject; response is the
  fake's matches serialized.
- `source="buganizer"` → 400.

**New `tests/unit/test_broker_sources.py`** (agent proxies):
- Unconfigured (`base_url=""`) → `[]` and no fetch call.
- Configured → `fetch_fn` receives (`"google_docs"`, query, `principal.email`,
  limit) and its `ContextMatch` list is returned unmodified.
- `fetch_fn` raising → exception propagates (the registry's `search_all` is the
  containment layer — assert that via the existing registry test pattern).

**Extend `tests/unit/test_context_source_registry.py`**:
- `build_sources` with the new three-entry config builds three sources in order.

**Extend `tests/unit/test_relevance.py`**: import `rank` from `weave_common` and via
the old `agent.context_sources.relevance` path; same results.

`make lint && make test` green before any deploy.

## Step 6 — rollout order and live verification

Standing constraint: the assistant runs `tofu plan -out=…` only; the user applies.

1. **Infra first**: `tofu plan` → user applies (enables Tasks API, adds
   `agent_invoker`). Then the **manual DWD scope edit** (step 4.4) — do this early
   so propagation overlaps with the rest.
2. **Ingestion image**: build/push, bump `image_tag` in `deployed.auto.tfvars`,
   plan → user applies. The broker route is now live but has no callers — safe.
3. **Smoke the broker directly** (V1): mint an ID token as yourself is *not*
   equivalent (wrong SA) — instead verify the 403 path with a plain
   `curl -X POST <ingestion_url>/context/search` (expect 403), and verify DWD
   propagation with a one-off script using `delegated_credentials` locally
   (impersonation as an onboarded user, `tasklists().list()` + a `files.list`
   fullText query must both return 200).
4. **Agent deploy** (V2): `make deploy-agent` with the two new env vars exported;
   update `agent_engine_id` in `deployed.auto.tfvars`; plan → user applies.
   Immediately check agent logs for `fetch_id_token` failures — this is the one
   mechanism unverified on Agent Engine (fallback documented in Step 2).
5. **End-to-end replay** (V3): clear the `processed_meetings` lease for the standing
   test conference, republish to `meet-artifacts`, and confirm in logs:
   - broker route logged searches with the owner as subject (one per
     `search_related_context` call, sources `google_docs` and `google_tasks`);
   - no 403s from the agent → broker hop;
   - the enrichment output's kept/rejected behavior looks sane on the delivered
     card (Details mentions a document/task only when genuinely relevant).
   Seed determinism for the test: create one Google Task and one Drive doc for the
   test user whose titles overlap an expected action item before replaying.
6. `tofu plan` converges to "No changes"; 403-curl from step 3 re-run once more
   after everything is live (defense-in-depth still intact).

## Effort/order summary for an implementing agent

Steps 0→3 are one PR-sized change (shared schema, broker, proxies, prompt, tests);
Step 4 is a small Terraform diff plus one manual Admin Console edit; Steps 5–6 gate
the deploy. Nothing in Steps 0–3 activates until both the env vars (Step 2) and the
DWD scope (Step 4) exist, so the rollout is safe in exactly the written order.
