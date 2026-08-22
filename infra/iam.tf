resource "google_service_account" "ingestion" {
  account_id   = "weave-ingestion-sa"
  display_name = "Weave ingestion service (only SA with domain-wide delegation)"
}

resource "google_service_account" "pubsub_push" {
  account_id   = "weave-pubsub-push-sa"
  display_name = "Weave Pub/Sub push identity (run.invoker on ingestion only)"
}

resource "google_service_account" "agent" {
  account_id   = "weave-agent-sa"
  display_name = "Weave Agent Engine runtime (no delegation, ever)"
}

resource "google_service_account" "subscriptions" {
  account_id   = "weave-subscriptions-sa"
  display_name = "Weave per-user Meet subscription manager"
}

locals {
  ingestion_roles = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/modelarmor.user",
    "roles/logging.logWriter",
  ]
  agent_roles = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/modelarmor.user",
  ]
}

resource "google_project_iam_member" "ingestion" {
  for_each = toset(local.ingestion_roles)
  project  = var.project_id
  role     = each.value
  member   = google_service_account.ingestion.member
}

resource "google_project_iam_member" "agent" {
  for_each = toset(local.agent_roles)
  project  = var.project_id
  role     = each.value
  member   = google_service_account.agent.member
}

# The subscription job owns onboarding lifecycle reconciliation in Firestore.
resource "google_project_iam_member" "subscriptions_datastore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = google_service_account.subscriptions.member
}

# Key-less domain-wide delegation: these SAs sign their own DWD assertions via
# the IAM Credentials API instead of carrying exported keys.
resource "google_service_account_iam_member" "ingestion_self_signer" {
  service_account_id = google_service_account.ingestion.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.ingestion.member
}

resource "google_service_account_iam_member" "subscriptions_self_signer" {
  service_account_id = google_service_account.subscriptions.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.subscriptions.member
}

# The Pub/Sub service agent mints OIDC tokens as the push SA for push deliveries.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
