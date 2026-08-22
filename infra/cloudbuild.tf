# Dedicated build identity: the default compute SA is over-broad and is not
# granted access to the staging bucket.
resource "google_service_account" "build" {
  account_id   = "weave-build-sa"
  display_name = "Weave Cloud Build runner"
}

resource "google_storage_bucket_iam_member" "build_staging" {
  bucket = google_storage_bucket.adk_staging.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.build.member
}

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  repository = google_artifact_registry_repository.weave.id
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.build.member
}

resource "google_project_iam_member" "build_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build.member
}
