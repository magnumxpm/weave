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

## 7. Later passes

- D2 deploys the agent (`make deploy-agent`), producing `AGENT_ENGINE_ID`.
- D3: `make infra-pass2 AGENT_ENGINE_ID=... IMAGE_TAG=$(git rev-parse --short HEAD)`
  adds Cloud Run + the authenticated push subscription.
- Meet transcription must be ON for test users (Admin Console → Apps → Google
  Workspace → Google Meet), and note any explicit-consent policy for recordings.
