resource "google_artifact_registry_repository" "weave" {
  repository_id = "weave"
  location      = var.region
  format        = "DOCKER"
  description   = "Weave service images (sha-tagged only; never :latest)"
  depends_on    = [google_project_service.required]
}
