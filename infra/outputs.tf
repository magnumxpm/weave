output "project_id" {
  value = var.project_id
}

output "project_number" {
  value = data.google_project.this.number
}

output "ingestion_url" {
  value = var.create_cloud_run ? google_cloud_run_v2_service.ingestion[0].uri : ""
}

output "pubsub_push_audience" {
  value = var.pubsub_push_audience
}

output "ingestion_sa_email" {
  value = google_service_account.ingestion.email
}

output "ingestion_sa_unique_id" {
  description = "Client ID to authorise for domain-wide delegation in the Admin Console."
  value       = google_service_account.ingestion.unique_id
}

output "pubsub_push_sa_email" {
  value = google_service_account.pubsub_push.email
}

output "agent_sa_email" {
  value = google_service_account.agent.email
}

output "subscriptions_sa_email" {
  value = google_service_account.subscriptions.email
}

output "subscriptions_sa_unique_id" {
  description = "Client ID to authorise for domain-wide delegation in the Admin Console."
  value       = google_service_account.subscriptions.unique_id
}

output "meet_artifacts_topic" {
  value = google_pubsub_topic.meet_artifacts.id
}

output "chat_events_topic" {
  description = "Pub/Sub connection target configured on the internal Google Chat app."
  value       = google_pubsub_topic.chat_events.id
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.weave.repository_id}"
}

output "chat_service_url" {
  value       = var.create_chat_service ? google_cloud_run_v2_service.chat[0].uri : ""
  description = "Paste into the Chat console as App URL, and as the Authentication Audience."
}
