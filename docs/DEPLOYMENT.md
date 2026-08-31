# Deployment

Two environments, and you can be productive in the first one before you have any cloud
access at all.

| | What it gives you | What it needs |
|---|---|---|
| **[Local](#local-environment)** | The whole pipeline on a bundled transcript, 265 tests, both agents in a browser | Python 3.12 and `uv`. A model key only for the model-backed parts. |
| **[Production](#production-environment)** | Live Meet ingestion, Chat onboarding, cards and the copilot | A GCP project, and Workspace admin for the Workspace tiers |

`infra/SETUP.md` is the terse companion to this document: the same procedure with the
Terraform specifics and the full table of traps hit while building it.

---

# Local environment

The local foundation — contracts, both agents, the context framework, reconciliation and
the delivery contract — runs with **no cloud dependencies at all**, so the entire pipeline
is exercisable before any Workspace integration exists.

## Install

```bash
uv python install 3.12
make install
```

## Verify the installation

```bash
make lint            # ruff + format check
make test            # 265 hermetic tests — no network, no cloud, no model calls
```

`make test` is fully offline by design. If it passes, the contracts, both agent
definitions, the context framework, identity resolution, reconciliation, prioritisation,
the Chat event parser and the delivery contract are all behaving. Nothing about it depends
on a project existing.

## Run the pipeline end to end

```bash
make demo                              # the bundled sample transcript
make demo TRANSCRIPT=path/to/your.txt  # or one of your own, with a .attendees.json sidecar
```

This runs extraction, verification and per-owner enrichment over a transcript and prints the
card each owner would receive, so you can watch the two-phase pipeline work without deploying
anything.

## Model credentials — only where they are actually needed

`make demo`, `make eval` and the agents need a model. Copy `.env.example` to `.env` and
set **one** of:

```bash
GOOGLE_API_KEY=...                     # simplest for local work

# or Vertex AI with application-default credentials:
GOOGLE_GENAI_USE_VERTEXAI=1
GOOGLE_CLOUD_PROJECT=<your-project>
GOOGLE_CLOUD_LOCATION=us-central1
```

`WORKSPACE_TIMEZONE` (an IANA name such as `Asia/Kolkata`) is required wherever dates are
interpreted — it decides what "today", "yesterday" and "last Monday" mean. It is validated
and has no repository default on purpose.

Model-backed evaluation is deliberately kept out of the hermetic suite:

```bash
make eval            # LLM-judged extraction quality and isolation; needs a model key
```

## Work on prompts and tools against real data

```bash
make web PROJECT_ID=<project> WORKSPACE_TIMEZONE=Asia/Kolkata
```

Serves both agents through `adk web` at <http://127.0.0.1:8000> against the deployed
Firestore, so prompt and tool changes can be tried without a redeploy.

In the browser UI the extraction agent works normally, but every copilot tool comes back
empty. That is correct behaviour, not a fault: the development UI supplies a fixed
placeholder rather than an email, and Weave refuses to guess whose commitments to show. To
exercise the copilot locally as a real person, call the server's API with an address:

```bash
U=me@example.com; S=$RANDOM
curl -s -X POST "http://127.0.0.1:8000/apps/agent.copilot/users/$U/sessions/$S" \
  -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8000/run -H 'Content-Type: application/json' -d "{
  \"app_name\":\"agent.copilot\", \"user_id\":\"$U\", \"session_id\":\"$S\",
  \"new_message\":{\"role\":\"user\",\"parts\":[{\"text\":\"what do I need to do?\"}]}}"
```

## What local cannot do

Local runs the reasoning; it does not subscribe to Meet, deliver Chat cards, or answer a
button click. Everything else, including the parts most likely to be wrong, is exercisable
on your own machine.

---

# Production environment

End-to-end procedure for standing Weave up in a fresh GCP project and Google Workspace
tenant. Everything scriptable is Terraform (OpenTofu-compatible); the handful of steps
Google only exposes in a browser are called out explicitly.

**Time to a working system:** ~30 minutes for the pipeline, plus delegation propagation.

## The tiers, and what each one needs

Work through them in order. Everything up to and including step 4 runs on project roles
alone, on bundled fixture transcripts — which is a genuinely working system exercising every
deployed code path, and the right place to be while Workspace approvals are pending.

| Tier | Unlocks | Requires |
|---|---|---|
| **0 — Fixture pipeline** | The deployed pipeline end to end on a bundled transcript | Project roles only |
| **1 — Live ingestion** | Meetings ingest automatically as they end | Org-policy exception + domain-wide delegation |
| **2 — Google Chat** | Self-service onboarding, DM cards with working buttons | Chat app configuration + a publisher binding |
| **3 — Copilot** | Conversation in the same DM | A second Agent Engine deployment |

## 0 · Prerequisites

```bash
export PROJECT_ID=<your-project>
export REGION=us-central1
export WORKSPACE_TIMEZONE=<IANA_TIMEZONE>   # e.g. Asia/Kolkata — required, no default

gcloud auth login
gcloud auth application-default login
gcloud auth application-default set-quota-project "$PROJECT_ID"
```

- OpenTofu (`brew install opentofu`) or Terraform ≥ 1.6.
- The project exists, is linked to billing, and — for Workspace integration — sits under the
  Workspace organisation.
- Python 3.12 and `uv`.

`WORKSPACE_TIMEZONE` is not cosmetic: it decides what "today", "yesterday" and "last Monday"
mean for both meeting dates and copilot queries. It is validated, and has no repository
default on purpose.

### Project roles

Typically granted by a cloud baseline: `aiplatform.admin`, `artifactregistry.admin`,
`run.admin`, `storage.admin`, `secretmanager.admin`, `cloudbuild.builds.editor`,
`serviceusage.serviceUsageAdmin`, `iam.serviceAccountAdmin`, `iam.serviceAccountUser`,
`resourcemanager.projectIamAdmin`, `viewer`.

Usually must be added (self-grantable with `projectIamAdmin`):

| Role | Why |
|---|---|
| `roles/datastore.owner` | Firestore database and its eight indexes |
| `roles/pubsub.admin` | Topics, subscriptions, topic-level IAM |
| `roles/cloudscheduler.admin` | The subscription-manager schedule |
| `roles/modelarmor.admin` | Model Armor **ignores basic roles entirely** |
| `roles/orgpolicy.policyAdmin` | **Tier 1 only** — the domain-restricted-sharing exception |

Model Armor is also regional-only, and the bundled CLI surface reaches the global endpoint
and 403s. Fix once per workstation:

```bash
gcloud config set api_endpoint_overrides/modelarmor \
  "https://modelarmor.$REGION.rep.googleapis.com/"
```

## 1 · Bootstrap APIs

Terraform needs three APIs before it can manage the other sixteen:

```bash
gcloud services enable serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com orgpolicy.googleapis.com --project="$PROJECT_ID"
```

`apis.tf` manages the rest: `aiplatform`, `firestore`, `run`, `pubsub`, `artifactregistry`,
`cloudbuild`, `cloudscheduler`, `modelarmor`, `secretmanager`, `logging`, `meet`, `drive`,
`tasks`, `admin`, `chat`, `workspaceevents`. Enabling them early is harmless — Terraform
adopts them.

## 2 · Terraform pass 1 — foundations

```bash
# edit infra/terraform.tfvars: project_id, region
make infra-init
make infra-pass1 WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"
```

Creates the four service accounts and their roles, Pub/Sub topics, the DLQ subscription and
the Meet publisher binding, the project-scoped `iam.allowedPolicyMemberDomains` exception,
Firestore in native mode with all eight indexes, both Model Armor templates, Artifact
Registry, the build service account, and the paused Scheduler job.

**Record the two `*_unique_id` outputs** — they are the client IDs for delegation in step 6.

### Verify before continuing

```bash
make infra-plan WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"   # must say "No changes"
gcloud firestore indexes composite list --project="$PROJECT_ID"  # all eight READY
```

Do not proceed until the plan converges. Drift here compounds later.

## 3 · Deploy the pipeline agent

```bash
export AGENT_SA="weave-agent-sa@$PROJECT_ID.iam.gserviceaccount.com"
make deploy-agent BOOTSTRAP_WITHOUT_CONTEXT_BROKER=1
# prints AGENT_ENGINE_ID=projects/.../reasoningEngines/N
```

`BOOTSTRAP_WITHOUT_CONTEXT_BROKER=1` exists **only** for this initial dependency cycle: the
agent needs the broker URL, which comes from ingestion, which needs the agent id. On this
first pass the Google sources safely return no matches. Every later deployment must use the
populated Terraform outputs, and `make deploy-agent` refuses without them.

> **Model names are backend-specific.** Agent Engine runs on Vertex, which serves a different
> catalogue than AI Studio, and the two do not carry the same names at the same time. The
> default is `gemini-3.5-flash`, overridable with `WEAVE_MODEL` — set it to whatever your
> project actually serves. A name the backend does not serve fails every *meeting* at
> "no final response" rather than failing the deploy, so confirm it first:
>
> ```bash
> curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
>   -H "Content-Type: application/json" \
>   "https://$REGION-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$REGION/publishers/google/models/${MODEL}:generateContent" \
>   -d '{"contents":[{"role":"user","parts":[{"text":"ok"}]}]}'
> ```
>
> Brace the variable — zsh reads a bare `$MODEL:generateContent` as a history modifier and
> silently mangles the URL into a 404.

## 4 · Build and deploy ingestion — Tier 0

```bash
make build-image
make infra-pass2 AGENT_ENGINE_ID=<from step 3> \
  IMAGE_TAG=$(git rev-parse --short HEAD) \
  WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"
```

Pass 2 defaults to `artifact_source=fixture` and `delivery_mode=log`, so the whole pipeline
is exercisable before any Workspace wiring exists.

### Smoke test

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST <INGESTION_URL>/pubsub-push   # expect 403

gcloud pubsub topics publish meet-artifacts --project="$PROJECT_ID" \
  --message='{"transcript":{"name":"conferenceRecords/smoke-0001/transcripts/t1"}}'
```

Expect one `meeting processed` log line, `processed_meetings/smoke-0001.status=delivered`,
and one `action_items` document per actionable owner. **Re-publishing the same id must return
200 and write nothing new** — that is the idempotency check, and it is worth doing explicitly.

## 5 · Tier 1a — the domain-restricted-sharing exception

Google Workspace Events publishes Meet notifications as its own system account,
`meet-api-event-push@system.gserviceaccount.com`. Organisations that enforce
`constraints/iam.allowedPolicyMemberDomains` block granting it `pubsub.publisher`, and
without that binding **no transcript ever reaches the pipeline**.

`infra/org_policy.tf` creates a **project-scoped** exception. Note precisely what it is: it
applies to this one project, never to the folder or organisation, and exists so one
Google-owned publisher can write to one topic.

```bash
gcloud organizations add-iam-policy-binding <ORG_ID> \
  --member=user:<YOU> --role=roles/orgpolicy.policyAdmin
```

Find `<ORG_ID>` with `gcloud organizations list`. If the role cannot be granted, this is an
escalation — there is no code-level workaround, and disabling the flag only moves the failure.

## 6 · Tier 1b — domain-wide delegation (Workspace Admin Console)

At <https://admin.google.com/ac/owl/domainwidedelegation> → **Add new**, twice, using the
client IDs from pass 1:

| Client ID | Scopes (comma-separated, one line) |
|---|---|
| `ingestion_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/tasks.readonly` |
| `subscriptions_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly` |

Every scope is `readonly`. **Editing an existing entry replaces its scopes rather than
appending**, so always paste the full line. Propagation takes minutes — a
`403 unauthorized_client` immediately afterwards means wait, not misconfigure.

Also required in the Admin Console:

- **Meet transcription on** for pilot users (Apps → Google Workspace → Google Meet).
- Check whether the **explicit-consent policy for transcripts** is enabled. If it is, a
  transcript exists only for meetings where a participant accepted the prompt — so a pilot
  meeting can legitimately produce nothing.

### Switch live ingestion on

```bash
cd infra && tofu apply -var artifact_source=live -var admin_subject=<workspace-admin> ...
```

`admin_subject` must be a Workspace admin and is used **only** for Directory lookups. Meet
reads impersonate the user whose subscription produced each event — a conference record is
visible only to that conference's participants, so a single fixed subject would restrict the
system to one person's meetings. Directory lookups use `admin_view`, which is why the subject
must be an admin: `domain_public` depends on domain contact sharing and otherwise resolves
only the caller.

## 7 · Subscription manager

```bash
make build-subscription-image

cd infra && tofu apply -var create_cloud_run=true -var image_tag=<tag> \
  -var agent_engine_id=<id> -var create_subscription_manager=true \
  -var subscription_manager_image_tag=<tag> \
  -var workspace_timezone="$WORKSPACE_TIMEZONE"

gcloud run jobs execute weave-subscription-manager --region="$REGION" --project="$PROJECT_ID"
gcloud scheduler jobs resume weave-subscription-manager --location="$REGION" --project="$PROJECT_ID"
```

The job reconciles `onboarded_users` to live Meet subscriptions and processes `offboarding`
tombstones by deleting the subscription before the record. One user's failure never stops the
sweep.

The Meet backend rejects an email target and the literal `me` with
`TARGET_RESOURCE_ACCESS_DENIED` — the target must be the **numeric Cloud Identity id**, which
is why onboarding records are keyed that way. The Chat install supplies it; for emergency
recovery only:

```bash
make onboard EMAIL=user@yourdomain USER_ID=<numeric-id> DM_SPACE=spaces/<id> \
  PROJECT_ID="$PROJECT_ID"
```

## 8 · Tier 2 — Google Chat

The Chat surface is where users onboard themselves and where every card and answer lands.

```bash
make build-chat-image
cd infra && tofu apply -var create_chat_service=true -var chat_image_tag=<tag> ...
# read the URL back, then apply again with it as the audience
tofu -chdir=infra output -raw chat_service_url
cd infra && tofu apply -var chat_audience=<that URL> -var delivery_mode=chat \
  -var admin_subject=<workspace-admin> ...
```

Then configure the app in **Cloud Console → APIs & Services → Google Chat API →
Configuration**:

- App name `Weave`, an HTTPS avatar URL, a short description.
- Interactive features **on**; direct messages **on**.
- Connection settings: **App URL** = `chat_service_url`, **Authentication Audience: HTTP
  endpoint URL** (not Project Number).
- **Join spaces and group conversations: off.** Weave is DM-only, and that checkbox is what
  `ADDED_TO_SPACE` depends on — with it off, a DM install arrives as a `MESSAGE` event, which
  is the onboarding signal the handler acts on.
- Visibility: make the internal app discoverable across the domain, or to a pilot group.
- Confirm the **Service account email** shown is one of the two publishers granted in
  `chat_events.tf`. An add-on-style app publishes as
  `service-<project-number>@gcp-sa-gsuiteaddons.iam.gserviceaccount.com`, not as
  `chat-api-push`. Getting this wrong is silent: Chat publishes nothing and the topic stays
  empty.

Save; the app status should read LIVE.

**Why App URL rather than Pub/Sub.** A Pub/Sub-connected Chat app has no channel to answer an
interaction on — Google's own documentation says such an app "can't update individual cards
with a synchronous response". In practice a button click never reaches the service and the
user sees a red failure. **Authentication Audience: HTTP endpoint URL** makes the bearer a
Google-signed OIDC token verified by the same code path already used for Pub/Sub push; if the
audience is wrong the service answers 401, which is immediate and visible rather than silent.
The switch is reversible: pointing Connection settings back at the topic restores the previous
behaviour with no redeploy.

**The service account email also tells you which envelope dialect to expect.** Both are
handled — `parse_chat_event` accepts either, and `weave_chat.responses` picks the matching
reply dialect from the request itself, logging `envelope_dialect` on every call. Read that log
line after the first click rather than assuming which shape arrived.

**User onboarding.** Each user opens Chat → New chat → Weave and **sends any message**. That
stores their numeric id and exact DM space, submits an immediate subscription sweep, and
answers with a welcome card. The install is only an opt-in signal — delegation remains the
authority for reading Meet data, and adding the app grants no new permissions.

Do **not** force-install the app organisation-wide for self-serve mode: managed users may be
unable to remove it themselves. If this deployment was previously managed-installed, verify
the event endpoint first, then remove the managed installation and have users add Weave
individually.

## 9 · Tier 3 — the copilot

The copilot is a second Agent Engine deployment on the same `weave-agent-sa` — same Firestore
and Vertex roles, no delegation, delegated reads only through `/context/search`.

```bash
export AGENT_SA="weave-agent-sa@$PROJECT_ID.iam.gserviceaccount.com"
make deploy-copilot WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"
# prints COPILOT_ENGINE_ID=projects/.../reasoningEngines/N
```

Set `copilot_engine_id` to that resource name and apply. **An empty value is the rollback
switch**: Chat messages keep their onboarding behaviour and simply do not invoke the copilot.
The Chat push subscription uses a 600-second ack deadline to match Cloud Run.

Then, in Chat, ask *"what needs my attention?"* as two different onboarded users and verify
that each answer contains only that owner's commitments. Click **Mark done** on a delivered
card; only the linked Weave commitment should close, and replaying the click must be harmless.

## 10 · Rolling out the search-backed capabilities

Semantic history and the commitment graph each depend on a Firestore index, and both follow
the same order. Review the plan carefully before applying index changes — Firestore index
field changes are ForceNew, and a replacement is a real availability event.

1. Apply the index and wait until it reports `READY`.
2. Deploy **ingestion first**, so new writes carry the new fields. The pipeline agent and
   ingestion share a data contract; deploying a schema-widening agent ahead of ingestion fails
   every meeting at `PipelineResult.model_validate`.
3. Backfill the existing records.
4. Deploy the agent, then update the configured engine id.

```bash
make backfill-embeddings PROJECT_ID="$PROJECT_ID"    # semantic history
make backfill-commitments PROJECT_ID="$PROJECT_ID"   # commitment graph
```

Both are safe to re-run. Verification that actually catches failure is in
[Running Weave](operations/running.md#changing-things-safely).

## 11 · Acceptance checklist

- [ ] `tofu plan` reports no drift with the committed tfvars and `WORKSPACE_TIMEZONE`.
- [ ] All eight Firestore composite indexes report `READY`.
- [ ] Unauthenticated `POST` to `/pubsub-push`, `/context/search` and `/chat-events` each `403`;
      an unsigned `POST` to the Chat service returns `401`.
- [ ] A fixture smoke meeting produces a `meeting_summaries` document, one `action_items`
      document per actionable owner **each carrying an `embedding` field**, and reconciled
      `commitments`. Replaying the same id returns `200` and writes nothing new.
- [ ] A second fixture meeting yields at least one commitment with `mention_count > 1` whose
      mention subdocuments name **different meetings**, and the ingestion logs contain no
      `commitment judgment failed` or `commitment vector lookup failed`.
- [ ] A pilot user adds Weave in Chat, receives the welcome card, and a Meet subscription
      appears for them on the next sweep.
- [ ] A real meeting between two pilot users delivers one card each, and neither card
      contains the other's items.
- [ ] **Mark done** closes exactly one commitment, redraws the card, and is harmless when
      replayed.
- [ ] Two users ask the same question in Chat and each answer contains only their own work.
- [ ] No stale project id, project number, or old service URL appears anywhere in
      configuration.

## Where to go next

[Running Weave](operations/running.md) — onboarding, health, safe change, and the
troubleshooting that actually catches failure. `infra/SETUP.md` keeps the full table of traps
hit while building this, each of which cost a debugging cycle.
