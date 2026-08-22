resource "google_pubsub_topic" "meet_artifacts" {
  name       = "meet-artifacts"
  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic" "meet_artifacts_dlq" {
  name       = "meet-artifacts-dlq"
  depends_on = [google_project_service.required]
}

# Google's Meet event publisher must be able to publish Workspace Events here.
resource "google_pubsub_topic_iam_member" "meet_publisher" {
  topic  = google_pubsub_topic.meet_artifacts.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:meet-api-event-push@system.gserviceaccount.com"
}

# The Pub/Sub service agent forwards dead-lettered messages to the DLQ topic.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  topic  = google_pubsub_topic.meet_artifacts_dlq.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

# Retain dead-lettered messages for inspection; without a subscription they vanish.
resource "google_pubsub_subscription" "dlq" {
  name                       = "meet-artifacts-dlq-sub"
  topic                      = google_pubsub_topic.meet_artifacts_dlq.id
  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }
}
