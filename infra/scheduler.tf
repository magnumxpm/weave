# Paused until the subscription_manager Cloud Run job exists (build plan D4).
resource "google_cloud_scheduler_job" "subscription_manager" {
  name      = "weave-subscription-manager"
  region    = var.region
  schedule  = "0 */6 * * *"
  time_zone = "Etc/UTC"
  # Enabled once the subscription manager job is deployed and verified.
  paused = !var.create_subscription_manager

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/weave-subscription-manager:run"

    oauth_token {
      service_account_email = google_service_account.subscriptions.email
    }
  }

  depends_on = [google_project_service.required]
}
