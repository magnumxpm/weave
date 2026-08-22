# Pass 2 (create_cloud_run=true): ingestion service + authenticated push subscription.

resource "google_cloud_run_v2_service" "ingestion" {
  count    = var.create_cloud_run ? 1 : 0
  name     = "weave-ingestion"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL" # no allUsers binding; OIDC on the push sub is the control

  # Cloud Run rejects any OIDC token whose `aud` is not the service URL unless
  # that value is declared here. The fixed audience is what breaks the
  # Terraform self-reference cycle, so it must be registered explicitly.
  custom_audiences = [var.pubsub_push_audience]

  # Service-level scaling is server-populated; declaring it avoids a permanent
  # diff against the revision-level block inside `template`.
  scaling {
    min_instance_count = 0
  }

  template {
    service_account                  = google_service_account.ingestion.email
    timeout                          = "600s"
    max_instance_request_concurrency = 4

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.weave.repository_id}/ingestion:${var.image_tag}"

      resources {
        cpu_idle = false # transcript processing continues after the HTTP response begins
      }

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "AGENT_ENGINE_ID"
        value = var.agent_engine_id
      }
      env {
        name  = "PUBSUB_PUSH_SA"
        value = google_service_account.pubsub_push.email
      }
      env {
        name  = "PUBSUB_PUSH_AUDIENCE"
        value = var.pubsub_push_audience
      }
      env {
        name  = "MODEL_ARMOR_INPUT_TEMPLATE"
        value = google_model_armor_template.transcript_input.id
      }
      # Swap to live + a real subject once DWD propagation is confirmed.
      env {
        name  = "ARTIFACT_SOURCE"
        value = var.artifact_source
      }
      env {
        name  = "FIXTURE_DIR"
        value = "/app/services/ingestion/fixtures"
      }
      env {
        name  = "ADMIN_SUBJECT"
        value = var.admin_subject
      }
      env {
        name  = "DELIVERY_MODE"
        value = var.delivery_mode
      }
    }

    scaling {
      max_instance_count = 3
      min_instance_count = 0
    }
  }

  lifecycle {
    precondition {
      condition     = var.image_tag != "" && var.agent_engine_id != ""
      error_message = "Pass 2 requires image_tag and agent_engine_id."
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "push_invoker" {
  count    = var.create_cloud_run ? 1 : 0
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.pubsub_push.member
}

resource "google_pubsub_subscription" "meet_artifacts_push" {
  count                = var.create_cloud_run ? 1 : 0
  name                 = "meet-artifacts-push"
  topic                = google_pubsub_topic.meet_artifacts.id
  ack_deadline_seconds = 600

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.ingestion[0].uri}/pubsub-push"

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = var.pubsub_push_audience
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.meet_artifacts_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  depends_on = [google_service_account_iam_member.pubsub_token_creator]
}
