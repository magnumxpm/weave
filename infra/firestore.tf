resource "google_firestore_database" "default" {
  name            = "(default)"
  location_id     = var.region
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "DELETE"
  depends_on      = [google_project_service.required]
}

# Backs the prior_meetings ACL query: visible_to CONTAINS + created_at DESC.
resource "google_firestore_index" "action_items_visibility" {
  database   = google_firestore_database.default.name
  collection = "action_items"

  fields {
    field_path   = "visible_to"
    array_config = "CONTAINS"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}
