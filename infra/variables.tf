variable "project_id" {
  type        = string
  description = "GCP project that hosts every Weave resource."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Region for Cloud Run, Artifact Registry, Firestore, and Scheduler."
}

variable "create_cloud_run" {
  type        = bool
  default     = false
  description = "Pass 2 switch: create the ingestion Cloud Run service and its push subscription."
}

variable "image_tag" {
  type        = string
  default     = ""
  description = "Git-sha image tag for the ingestion service (required when create_cloud_run=true)."
}

variable "agent_engine_id" {
  type        = string
  default     = ""
  description = "Full reasoning engine resource name (required when create_cloud_run=true)."
}

variable "create_subscription_manager" {
  type        = bool
  default     = false
  description = "Create the per-user subscription manager job (build plan D4)."
}

variable "subscription_manager_image_tag" {
  type        = string
  default     = ""
  description = "Git-sha image tag for the subscription manager job."
}

variable "artifact_source" {
  type        = string
  default     = "fixture"
  description = "Where transcripts come from: 'fixture' (bundled samples, no Workspace needed) or 'live' (Meet API via DWD)."

  validation {
    condition     = contains(["fixture", "live"], var.artifact_source)
    error_message = "artifact_source must be 'fixture' or 'live'."
  }
}

variable "admin_subject" {
  type        = string
  default     = ""
  description = "Workspace admin impersonated for Directory lookups only. Meet reads impersonate the user whose subscription produced each event. Required for artifact_source=live or delivery_mode=chat."
}

variable "delivery_mode" {
  type        = string
  default     = "log"
  description = "'log' renders cards to Cloud Logging; 'chat' DMs owners via the Chat app."

  validation {
    condition     = contains(["log", "chat"], var.delivery_mode)
    error_message = "delivery_mode must be 'log' or 'chat'."
  }
}

variable "manage_domain_restricted_sharing" {
  type        = bool
  default     = true
  description = "Create a project-scoped exception to iam.allowedPolicyMemberDomains (needed in Workspace orgs so Google's Meet event publisher can be granted pubsub.publisher). Applier needs orgpolicy.policyAdmin on the org."
}

variable "pubsub_push_audience" {
  type        = string
  default     = "weave-ingestion"
  description = "Fixed OIDC audience for the Pub/Sub push subscription; validated by the handler."
}
