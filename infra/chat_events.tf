# Google Chat interaction events are delivered to Pub/Sub, then pushed through
# the same authenticated Cloud Run boundary as Meet artifact events.
resource "google_pubsub_topic" "chat_events" {
  name       = "chat-events"
  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic_iam_member" "chat_publisher" {
  topic  = google_pubsub_topic.chat_events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:chat-api-push@system.gserviceaccount.com"
}

resource "google_pubsub_subscription" "chat_events_push" {
  count                = var.create_cloud_run ? 1 : 0
  name                 = "chat-events-push"
  topic                = google_pubsub_topic.chat_events.id
  ack_deadline_seconds = 60

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.ingestion[0].uri}/chat-events"

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
    maximum_backoff = "60s"
  }

  depends_on = [google_service_account_iam_member.pubsub_token_creator]
}
