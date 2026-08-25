# Google Chat's interaction endpoint. Public by necessity -- Chat authenticates
# with its own signed token and cannot present an IAM identity -- which is
# exactly why it is a separate service from weave-ingestion. Nothing here holds
# domain-wide delegation, and the only writes are to Weave's own commitments.

resource "google_service_account" "chat" {
  account_id   = "weave-chat-sa"
  display_name = "Weave Chat interaction endpoint (no delegation, ever)"
}

resource "google_project_iam_member" "chat" {
  for_each = toset(["roles/datastore.user", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.chat.member
}

resource "google_cloud_run_v2_service" "chat" {
  count               = var.create_chat_service ? 1 : 0
  name                = "weave-chat"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.chat.email
    # Chat gives an interaction a few tens of seconds; this is a backstop, not a
    # budget. Anything genuinely slow is republished rather than awaited.
    timeout                          = "60s"
    max_instance_request_concurrency = 20

    scaling {
      # A cold start inside Chat's interaction window would read to the user as
      # a broken button, so keep one instance warm.
      min_instance_count = 1
      max_instance_count = 5
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/weave/chat:${var.chat_image_tag}"

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      # Chat mints tokens for the audience configured in the console. With
      # Authentication Audience set to the endpoint URL that is this service's
      # own URL, which cannot reference itself here -- so it is filled in on a
      # second apply, the same two-pass shape as agent_engine_id.
      env {
        name  = "CHAT_AUDIENCE"
        value = var.chat_audience
      }
      env {
        name  = "CHAT_EVENTS_TOPIC"
        value = google_pubsub_topic.chat_events.id
      }
    }
  }
}
resource "google_cloud_run_v2_service_iam_member" "chat_public" {
  count    = var.create_chat_service ? 1 : 0
  name     = google_cloud_run_v2_service.chat[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_pubsub_topic_iam_member" "chat_service_publisher" {
  topic  = google_pubsub_topic.chat_events.name
  role   = "roles/pubsub.publisher"
  member = google_service_account.chat.member
}
