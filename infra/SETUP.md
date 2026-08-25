# Weave GCP setup — repeatable procedure

Everything scriptable lives in Terraform (`infra/`, OpenTofu-compatible). The few
steps Google only exposes in a browser console are listed at the end, in order.
Time to a working project from scratch: ~30 minutes plus DWD propagation.

## 0. Prerequisites

- `gcloud` CLI authenticated as a user with Owner on the target project
  (`gcloud auth login`), and ADC for Terraform
  (`gcloud auth application-default login`, then
  `gcloud auth application-default set-quota-project <PROJECT_ID>`).
- OpenTofu (`brew install opentofu`) or Terraform ≥ 1.6.
- The target project exists, is linked to billing, and — for Workspace
  integration — lives under the Workspace org.

## 1. One-time per organization

Grant yourself Org Policy Administrator (Workspace super admins can self-grant;
needed because the org enforces domain-restricted sharing, and pass 1 creates a
project-scoped exception so Google's Meet event publisher can be granted
`pubsub.publisher`):

```bash
gcloud organizations add-iam-policy-binding <ORG_ID> \
  --member=user:<YOU> --role=roles/orgpolicy.policyAdmin
```

Find `<ORG_ID>` with `gcloud organizations list`.

## 2. Bootstrap APIs (chicken-and-egg: Terraform needs these to run)

```bash
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  orgpolicy.googleapis.com --project=<PROJECT_ID>
```

The remaining 14 product APIs are managed by `apis.tf`; enabling them ahead of
time with `gcloud services enable` is also fine (Terraform adopts them).

## 3. Terraform pass 1

```bash
# edit infra/terraform.tfvars: project_id, region
make infra-init
make infra-pass1
```

Creates: 4 service accounts + roles, Pub/Sub topics + DLQ subscription + the
Meet publisher and service-agent bindings, the project-scoped
`iam.allowedPolicyMemberDomains` exception, Firestore native DB + the
`action_items` composite index, both Model Armor templates, Artifact Registry,
and the paused Scheduler job. Record the two `*_unique_id` outputs for step 5.

Notes:
- Model Armor is invisible to IAM basic roles; humans need
  `roles/modelarmor.admin` explicitly to inspect templates.
- The bundled `gcloud model-armor` surface may hit the global endpoint and 403.
  Fix: `gcloud config set api_endpoint_overrides/modelarmor
  https://modelarmor.<REGION>.rep.googleapis.com/`.

## 4. Verify pass 1

```bash
make infra-plan          # must converge to "No changes"
gcloud firestore indexes composite list --project=<PROJECT_ID>   # both indexes READY
```

## 5. Manual: domain-wide delegation (Workspace Admin Console — not Cloud Console)

https://admin.google.com/ac/owl/domainwidedelegation → **Add new**, twice:

| Client ID (Terraform output) | Scopes (comma-separated, one line) |
|---|---|
| `ingestion_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/tasks.readonly` |
| `subscriptions_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly` |

Propagation takes minutes; `403 unauthorized_client` afterwards means wait.

## 6. Manual: Google Chat app

**6a. Configure** (Cloud Console → APIs & Services → Google Chat API →
**Configuration** tab):
- App name `Weave`, any HTTPS avatar URL, short description.
- Interactive features: **on**; allow direct messages.
- Connection settings: **Cloud Pub/Sub topic**, using the
  `chat_events_topic` Terraform output.
- Visibility: make the internal app discoverable throughout the Workspace
  domain (or a test group during rollout).
- Save; app status should read LIVE.

Leave **Join spaces and group conversations** off: Weave delivers to direct
messages only. That checkbox is what `ADDED_TO_SPACE` depends on, so with it
off a direct-message install arrives as a `MESSAGE` event — which is the
onboarding signal the handler acts on. Confirm the **Service account email**
shown under Connection settings is one of the two publishers granted in
`chat_events.tf`; an add-on-style app publishes as
`service-<project-number>@gcp-sa-gsuiteaddons.iam.gserviceaccount.com`, not as
`chat-api-push`. Getting this wrong is silent: Chat publishes nothing and the
topic simply stays empty.

**6b. User onboarding.** Availability and installation are intentionally
different. Each user chooses Chat → New chat → Weave and **sends it any
message**. That event stores the user's numeric id and exact DM space in
Firestore, then submits an immediate subscription-manager sweep. A welcome card
confirms that provisioning was queued and introduces commitment questions. Once
`COPILOT_ENGINE_ID` is configured, later messages are sent to the owner-scoped
copilot. Removal is not always signalled for
direct messages, so offboard with the ledger if a user must be removed.

Do not force-install the app across the organisation for self-serve mode:
managed users might not be able to remove it themselves. If this deployment was
previously managed-installed, enable and verify the event endpoint first, then
remove the managed installation and have existing users add Weave individually.
An administrator-managed install remains a bulk-onboarding alternative, but it
has different offboarding semantics.

The Chat install is only an opt-in signal. Domain-wide delegation remains the
authority used to read Meet data; adding the app grants no new data permissions.

## 7. Deploy the agent (D2)

```bash
export PROJECT_ID=<PROJECT_ID> REGION=us-central1
export AGENT_SA=weave-agent-sa@$PROJECT_ID.iam.gserviceaccount.com
make deploy-agent BOOTSTRAP_WITHOUT_CONTEXT_BROKER=1
# prints AGENT_ENGINE_ID=projects/.../reasoningEngines/N
```

`make deploy-agent` reads `CONTEXT_BROKER_URL` and `CONTEXT_BROKER_AUDIENCE`
from Terraform outputs and normally fails if either is absent. The explicit bootstrap
flag above is only for the initial dependency cycle: those first-pass sources safely
return no Google matches until ingestion exists. All subsequent agent deployments must
use the populated outputs. Explicit environment values may be supplied when deploying
against a different reviewed environment.

**Model names are backend-specific.** Agent Engine runs on Vertex, which serves
a different catalogue than AI Studio: `gemini-3.x` names resolve with an API key
but 404 on Vertex. `agent/config.py` defaults to `gemini-2.5-flash` and honours
`WEAVE_MODEL`. Confirm before deploying:

```bash
curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://$REGION-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$REGION/publishers/google/models/${MODEL}:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"ok"}]}]}'
```
(Brace the variable — zsh parses a bare `$MODEL:generateContent` as a history
modifier and silently mangles the URL into a 404.)

## 8. Build and deploy the ingestion service (D3)

```bash
make build-image             # Cloud Build, sha tag, dedicated weave-build-sa
make infra-pass2 AGENT_ENGINE_ID=<from D2> IMAGE_TAG=$(git rev-parse --short HEAD)
```

Pass 2 defaults to `artifact_source=fixture` and `delivery_mode=log`, so the
whole pipeline is exercisable before Workspace is wired. Switch with
`-var artifact_source=live -var delivery_mode=chat -var admin_subject=<admin>`.

`admin_subject` is used **only** for Directory lookups. Meet reads impersonate
the user whose subscription produced each event, read from the CloudEvent
source — conference records are visible only to that conference's participants,
so a single fixed subject would restrict the system to one person's meetings.
When that id cannot be determined the event fails with the full attribute set
logged, rather than reading as the wrong account.

Verify:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST <URL>/pubsub-push   # 403
gcloud pubsub topics publish meet-artifacts --project=$PROJECT_ID \
  --message='{"transcript":{"name":"conferenceRecords/smoke-0001/transcripts/t1"}}'
```
Expect one `meeting processed` log line, `processed_meetings/<id>.status=delivered`,
and one `action_items` doc per actionable owner. Re-publishing the same id must
return 200 without writing again.

When rolling out the delivery gate before Chat events, seed every currently
served user before deploying the new ingestion image:

```bash
make onboard EMAIL=user@yourdomain USER_ID=<numeric-id> DM_SPACE=spaces/<id>
```

Without that migration, action-item history is still written but Chat delivery
is intentionally recorded as `skipped_not_onboarded`.

## 9. Subscription manager and onboarding

```bash
make build-subscription-image

cd infra && tofu apply -var create_cloud_run=true -var image_tag=<tag> \
  -var agent_engine_id=<id> -var create_subscription_manager=true \
  -var subscription_manager_image_tag=<tag>

gcloud run jobs execute weave-subscription-manager --region=$REGION --project=$PROJECT_ID
gcloud scheduler jobs resume weave-subscription-manager --location=$REGION --project=$PROJECT_ID
```

The job reads `onboarded_users` from Firestore. Active documents are reconciled
to live Meet transcript subscriptions; `offboarding` tombstones cause the job
to delete subscriptions before deleting the document. One user's failure does
not block the rest and leaves their record for the next scheduled sweep.

The Chat event supplies the numeric Cloud Identity id. For emergency/manual
recovery only, seed a user with:

```bash
make onboard EMAIL=user@yourdomain USER_ID=<numeric-id> DM_SPACE=spaces/<id>
```

The subscription manager impersonates the stored email through DWD while using
the numeric id as the Workspace Events target. The Meet backend rejects an
email target and the literal `me` with `TARGET_RESOURCE_ACCESS_DENIED`.

Meet transcription must be ON for those users (Admin Console → Apps → Google
Workspace → Google Meet), and check whether the explicit-consent policy for
transcripts is enabled — if it is, a transcript only exists for meetings where a
participant accepted the prompt.

## 10. Semantic history and card UI rollout

For an existing deployment, roll this schema change out in this order:

1. Apply `google_firestore_index.action_items_vector` and wait until the index
   reports `READY`. The `visible_to` ACL field and 768-dimensional vector field
   deliberately live in the same index.
2. Build and deploy ingestion. It can render old agent output using the
   extracted description, and new writes include display prose and embeddings.
3. Run `make backfill-embeddings PROJECT_ID=<PROJECT_ID>`.
4. Deploy the updated agent, then update the configured Agent Engine id.

Documents without an `embedding` field are invisible to vector search. The
lexical fallback keeps searches available, but the backfill is a required
rollout step rather than optional cleanup. Confirm that no action-item document
lacks the field after it completes.

The meeting agenda comes from the transcript document's Drive filename. That
read is best-effort: a participant may not be able to read an organizer-owned
document, in which case the card keeps the meeting time and omits the agenda.
No additional DWD scope is required because `drive.readonly` is already granted.

## 11. Google Docs and Tasks context rollout

Owner-visible Drive files and open Google Tasks are searched through the ingestion
context broker. The Agent Engine service account has only `run.invoker` on ingestion
and still has no delegation. Roll out in this order:

1. Apply Terraform to enable the Tasks API and grant the agent service account
   `run.invoker` on ingestion.
2. In Workspace Admin domain-wide delegation, edit the existing ingestion service
   account entry and replace its scopes with the full four-scope line from §5.
   Editing replaces rather than appends; allow several minutes for propagation.
3. Build and deploy ingestion so `/context/search` is live. An unauthenticated POST
   must return 403. Verify Drive and Tasks delegated reads independently for one
   onboarded test user.
4. Deploy Agent Engine with `make deploy-agent`, then update the configured engine id.
   Check immediately for `fetch_id_token` failures. If Agent Engine cannot use its
   metadata identity endpoint, add a narrowly scoped self-signing fallback only after
   verifying that limitation; do not grant delegation to the agent.
5. Replay a test meeting after creating a clearly related document and task. Broker
   logs should name the owner subject, source, and result count but never the query.

A non-onboarded attendee receives no Docs or Tasks context. Any broker, API, or source
failure degrades to no results from that source and does not stop the other sources.

## 12. Commitment graph and copilot rollout

The copilot is a second Agent Engine deployment. It uses `weave-agent-sa`, which keeps its
existing Firestore and Vertex roles, has no domain-wide delegation, and reaches delegated
Docs/Tasks reads only through `/context/search`.

1. Apply the `commitments_vector` and `commitments_last_mentioned` Firestore indexes and
   wait for both to report `READY`. Review the plan carefully: Firestore index field changes
   are ForceNew and a replacement is a real availability event.
2. Deploy ingestion first. New meetings now write immutable action-item mentions and then
   reconcile them into owner-scoped `commitments`; reconciliation failure is recorded on
   the processed meeting but never fails delivery.
3. Run `make backfill-commitments PROJECT_ID=<PROJECT_ID>`. The command is replay-safe via
   UUIDv5 commitment ids and mention subdocument ids. Spot-check merges before exposing the
   copilot: same deliverable across meetings should merge; same topic alone must not.
4. Deploy the ADK app:

   ```bash
   export PROJECT_ID=<PROJECT_ID> REGION=us-central1
   export AGENT_SA=weave-agent-sa@$PROJECT_ID.iam.gserviceaccount.com
   make deploy-copilot
   # COPILOT_ENGINE_ID=projects/.../reasoningEngines/N
   ```

5. Set `copilot_engine_id` to that resource name and apply Terraform. An empty value is the
   rollback switch: Chat messages retain onboarding behavior and simply do not invoke the
   copilot. The Chat push subscription uses a 600-second ack deadline to match Cloud Run.
6. In Chat, ask “what needs my attention?” as two onboarded test users and verify that each
   answer contains only that owner's commitments. Click **Mark done** on a delivered card;
   only the linked Weave commitment should close, and replaying the click must be harmless.

### Gemini Enterprise registration

Google's current Agent Runtime registration documentation states that ADK agents receive
the invoking user's email. Weave still fails closed unless the runtime supplies that value
as an email-shaped ADK `user_id`; it never asks the model or user to identify the caller.
Before domain rollout, register the engine in Gemini Enterprise → app → Agents → Add agent →
Custom agent via Agent Runtime, using display name **Weave Commitment Copilot** and the full
`COPILOT_ENGINE_ID` resource path. No optional per-user OAuth authorization is needed because
Google reads remain brokered by ingestion.

Run the load-bearing identity tests before sharing it: invoke as user A and confirm logs and
tool results resolve A; invoke as user B and ask for A's commitments and confirm only B-owned
documents can be read. If the runtime does not pass the email as the verified ADK `user_id`,
unregister the agent and keep only Chat until the platform identity field is mapped in
`agent/copilot/principal.py`. Do not relax the validator.

To unregister, open the Gemini Enterprise app's Agents page and delete the registered agent,
or call the documented Discovery Engine agent-resource `DELETE`; deleting the registration
does not delete the Agent Engine deployment.

## Traps hit while building this (all cost a debugging cycle)

| Symptom | Cause | Fix |
|---|---|---|
| Push 401s at the platform layer, never reaches the container | Cloud Run only accepts an OIDC `aud` that is the service URL | declare `custom_audiences` on the service (already in `cloud_run.tf`) |
| Handler INFO logs missing in Cloud Logging | root logger defaults to WARNING under uvicorn | `logging_config.configure_logging()` at app creation |
| Cloud Build 403 on the source tarball | default compute SA has no access to the staging bucket | dedicated `weave-build-sa` (`cloudbuild.tf`) |
| `gcloud model-armor` 403s despite Owner | Model Armor ignores basic roles and is regional-only | grant `roles/modelarmor.admin`; set the regional `api_endpoint_overrides` |
| Agent Engine returns "no final response" | model name not on Vertex | see §7 |
| `gcloud builds submit --tag` rejects `-f` | `--tag` mode cannot set a Dockerfile path | use the `cloudbuild.yaml` configs in each service |
| Terraform/gcloud fail with `invalid_rapt` | Workspace reauth policy expired the session | `gcloud auth login` again |
| `tofu plan` 403s reading the org policy, citing a missing quota project | the Org Policy API refuses ADC calls without one, and ADC loses it on re-auth | already handled by `user_project_override` in `versions.tf`; restore ADC's own with `gcloud auth application-default set-quota-project <PROJECT_ID>` |
| `TARGET_RESOURCE_ACCESS_DENIED` creating a subscription | target must be the numeric Cloud Identity id | see §9 |
| User can find Weave but is never onboarded | Chat interactive features or the Pub/Sub connection is not enabled | see §6a; inspect `chat-events-push` |
| App installs work but `chat-events` never receives a message | the publisher named on the Configuration page is not the one granted | grant the service account that page displays; both variants are in `chat_events.tf` |
| Group-space add does not onboard anyone | v1 intentionally accepts direct-message installs only | add Weave through New chat |
| Offboarding document remains present | subscription deletion failed, so the tombstone is retained for retry | inspect the subscription-manager execution logs |
| Chat delivery uses Directory or 404s | legacy/manual onboarding record has no `dm_space` | reinstall Weave or repair it with `make onboard` |
| Meet fetch 403s for another user's meeting | reads must impersonate the subscribing user, not a fixed one | see §8 |
| Directory lookups 403 for everyone except the impersonated user | `domain_public` needs domain contact sharing and otherwise resolves only the caller | the client uses `admin_view`; keep `admin_subject` an admin |
| A meeting fails because one attendee cannot be resolved | external guests are not in the directory | they are dropped individually; only an all-participant failure raises |
| A loop variable before `:method` in a URL 404s in zsh | zsh reads `$var:g...` as a history modifier | brace it: `${var}:generateContent` |
| Cards deliver with a header and no items | enrichment echoed the item inexactly, so the fingerprint gate dropped everything | the orchestrator falls back to an unenriched bundle; check `dropping out-of-scope enriched item` warnings |
| Every meeting fails at `PipelineResult.model_validate` after an agent deploy | a schema-widening agent was deployed ahead of ingestion | deploy ingestion first, then redeploy the agent |
| Semantic history always uses lexical fallback | vector index is not ready, query embeddings are failing, or old documents were never backfilled | inspect `prior-meeting search` logs, wait for index `READY`, then run `make backfill-embeddings` |
| Recent action items never appear in semantic search | their documents have no `embedding` field | ingestion logs embedding failures without dropping history; repair credentials/quota and rerun the backfill |
