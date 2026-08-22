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
gcloud firestore indexes composite list --project=<PROJECT_ID>   # index READY
```

## 5. Manual: domain-wide delegation (Workspace Admin Console — not Cloud Console)

https://admin.google.com/ac/owl/domainwidedelegation → **Add new**, twice:

| Client ID (Terraform output) | Scopes (comma-separated, one line) |
|---|---|
| `ingestion_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly` |
| `subscriptions_sa_unique_id` | `https://www.googleapis.com/auth/meetings.space.readonly` |

Propagation takes minutes; `403 unauthorized_client` afterwards means wait.

## 6. Manual: Google Chat app (Cloud Console, correct project selected)

APIs & Services → Google Chat API → **Configuration** tab:
- App name `Weave`, any HTTPS avatar URL, short description.
- Interactive features: **off** (v1 sends cards; it never receives).
- Visibility: make available to your domain (or the test users).
- Save; app status should read LIVE.

## 7. Deploy the agent (D2)

```bash
export PROJECT_ID=<PROJECT_ID> REGION=us-central1
export AGENT_SA=weave-agent-sa@$PROJECT_ID.iam.gserviceaccount.com
make deploy-agent            # prints AGENT_ENGINE_ID=projects/.../reasoningEngines/N
```

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
`-var artifact_source=live -var workspace_subject=<user> -var delivery_mode=chat`.

Verify:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST <URL>/pubsub-push   # 403
gcloud pubsub topics publish meet-artifacts --project=$PROJECT_ID \
  --message='{"transcript":{"name":"conferenceRecords/smoke-0001/transcripts/t1"}}'
```
Expect one `meeting processed` log line, `processed_meetings/<id>.status=delivered`,
and one `action_items` doc per actionable owner. Re-publishing the same id must
return 200 without writing again.

## 9. Subscription manager (D4)

```bash
gcloud builds submit --project=$PROJECT_ID \
  --config=services/subscription_manager/cloudbuild.yaml \
  --substitutions=_IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/weave/subscription-manager:$(git rev-parse --short HEAD) \
  --gcs-source-staging-dir=gs://$PROJECT_ID-adk-staging/cloudbuild \
  --service-account=projects/$PROJECT_ID/serviceAccounts/weave-build-sa@$PROJECT_ID.iam.gserviceaccount.com .

cd infra && tofu apply -var create_cloud_run=true -var image_tag=<tag> \
  -var agent_engine_id=<id> -var create_subscription_manager=true \
  -var subscription_manager_image_tag=<tag> \
  -var 'onboarded_users=["user@yourdomain"]'

gcloud run jobs execute weave-subscription-manager --region=$REGION --project=$PROJECT_ID
gcloud scheduler jobs resume weave-subscription-manager --location=$REGION --project=$PROJECT_ID
```

`onboarded_users` takes **numeric Cloud Identity ids**, not emails: the Meet
backend rejects both an email address and the literal `me` with
`TARGET_RESOURCE_ACCESS_DENIED`. Get a user's id without any special access:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  https://openidconnect.googleapis.com/v1/userinfo    # "sub" is the id
```

To list emails instead, add `https://www.googleapis.com/auth/admin.directory.user.readonly`
to the subscriptions SA's delegation entry and pass a resolver; numeric ids need
only the Meet scope.

Meet transcription must be ON for those users (Admin Console → Apps → Google
Workspace → Google Meet), and check whether the explicit-consent policy for
transcripts is enabled — if it is, a transcript only exists for meetings where a
participant accepted the prompt.

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
| `TARGET_RESOURCE_ACCESS_DENIED` creating a subscription | target must be the numeric Cloud Identity id | see §9 |
| A loop variable before `:method` in a URL 404s in zsh | zsh reads `$var:g...` as a history modifier | brace it: `${var}:generateContent` |
