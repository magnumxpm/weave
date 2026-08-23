locals {
  services = [
    "admin.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "chat.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    # The meeting agenda is the transcript document's Drive title; the DWD scope
    # was already granted, but the API itself was never enabled here.
    "drive.googleapis.com",
    "firestore.googleapis.com",
    "logging.googleapis.com",
    "meet.googleapis.com",
    "modelarmor.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "workspaceevents.googleapis.com",
  ]
}

resource "google_project_service" "required" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "this" {}

# Materialise the Pub/Sub service agent so pass 1 can bind roles to it.
resource "google_project_service_identity" "pubsub" {
  provider   = google-beta
  service    = "pubsub.googleapis.com"
  depends_on = [google_project_service.required]
}
