# Workspace-created orgs enforce domain-restricted sharing by default, which
# blocks granting pubsub.publisher to Google's meet-api-event-push system
# account. Relax the constraint for this project only (never org-wide).
# The applier needs roles/orgpolicy.policyAdmin on the organization.
resource "google_org_policy_policy" "allow_google_system_accounts" {
  count  = var.manage_domain_restricted_sharing ? 1 : 0
  name   = "projects/${var.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent = "projects/${var.project_id}"

  spec {
    inherit_from_parent = false
    rules {
      allow_all = "TRUE"
    }
  }
}
