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

# Semantic prior-item search keeps the owner ACL in the vector query itself.
resource "google_firestore_index" "action_items_vector" {
  database   = google_firestore_database.default.name
  collection = "action_items"

  fields {
    field_path   = "visible_to"
    array_config = "CONTAINS"
  }

  # Firestore stores `__name__` between the filter and the vector field and
  # returns it on read. Omitting it here made every subsequent plan see drift,
  # and because each field is ForceNew, an unrelated apply silently destroyed
  # and recreated the index -- which is not a no-op: the destroy lands, the
  # recreate 409s on the still-reserved index id, and vector search is down
  # until it clears.
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }

  fields {
    field_path = "embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }
}

# Owner/date lookup answers "which commitments did I get today/Monday?" without
# scanning action items belonging to anyone else.
resource "google_firestore_index" "action_items_owner_date" {
  database   = google_firestore_database.default.name
  collection = "action_items"

  fields {
    field_path = "owner_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "meeting_date"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

# Attendee-scoped date retrieval for meeting summaries.
resource "google_firestore_index" "meeting_summaries_visibility" {
  database   = google_firestore_database.default.name
  collection = "meeting_summaries"

  fields {
    field_path   = "visible_to"
    array_config = "CONTAINS"
  }

  fields {
    field_path = "meeting_date"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

# Semantic summary search preserves the attendee ACL in the vector query.
resource "google_firestore_index" "meeting_summaries_vector" {
  database   = google_firestore_database.default.name
  collection = "meeting_summaries"

  fields {
    field_path   = "visible_to"
    array_config = "CONTAINS"
  }

  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }

  fields {
    field_path = "embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }
}

# Owner equality is part of the commitment vector lookup, so another owner's
# derived commitment cannot enter candidate reconciliation.
resource "google_firestore_index" "commitments_vector" {
  database   = google_firestore_database.default.name
  collection = "commitments"

  fields {
    field_path = "owner_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }

  fields {
    field_path = "embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }
}

resource "google_firestore_index" "commitments_last_mentioned" {
  database   = google_firestore_database.default.name
  collection = "commitments"

  fields {
    field_path = "owner_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "last_mentioned"
    order      = "ASCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }
}

# Reconciliation's lexical fallback takes the newest owner commitments before
# applying relevance ranking; keep that read distinct from the ascending stale query.
resource "google_firestore_index" "commitments_recent" {
  database   = google_firestore_database.default.name
  collection = "commitments"

  fields {
    field_path = "owner_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "last_mentioned"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}
