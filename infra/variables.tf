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
