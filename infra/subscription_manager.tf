# Per-user Meet subscriptions: there is no org-wide subscription, so this job
# sweeps the onboarded users and creates or renews one subscription each.
resource "google_cloud_run_v2_job" "subscription_manager" {
  count    = var.create_subscription_manager ? 1 : 0
  name     = "weave-subscription-manager"
  location = var.region

  template {
    template {
      service_account = google_service_account.subscriptions.email
      max_retries     = 1

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.weave.repository_id}/subscription-manager:${var.subscription_manager_image_tag}"

        env {
          name  = "MEET_TOPIC"
          value = google_pubsub_topic.meet_artifacts.id
        }
        env {
          name  = "SUBSCRIPTIONS_SA"
          value = google_service_account.subscriptions.email
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.subscription_manager_image_tag != ""
      error_message = "subscription_manager_image_tag is required when create_subscription_manager=true."
    }
  }
}

# The scheduler job created in pass 1 invokes this job; grant it permission.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  count    = var.create_subscription_manager ? 1 : 0
  name     = google_cloud_run_v2_job.subscription_manager[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.subscriptions.member
}

# Chat onboarding submits an immediate sweep, but cannot alter job settings.
resource "google_cloud_run_v2_job_iam_member" "ingestion_invoker" {
  count    = var.create_subscription_manager ? 1 : 0
  name     = google_cloud_run_v2_job.subscription_manager[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.ingestion.member
}
