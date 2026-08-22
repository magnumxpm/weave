resource "google_storage_bucket" "adk_staging" {
  name                        = "${var.project_id}-adk-staging"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  depends_on                  = [google_project_service.required]
}
