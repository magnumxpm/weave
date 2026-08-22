# Screens transcripts before they reach any model context (build plan C4).
resource "google_model_armor_template" "transcript_input" {
  location    = var.region
  template_id = "transcript-input"

  # The API always materialises this block; declaring it avoids a permanent diff.
  template_metadata {}

  filter_config {
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "HIGH"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "HIGH"
      }
    }
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "HIGH" # fail closed only at high confidence (v2 review decision)
    }
  }
}

# Screens agent output before delivery (wired via after_model_callback in D2).
resource "google_model_armor_template" "agent_output" {
  location    = var.region
  template_id = "agent-output"

  # The API always materialises this block; declaring it avoids a permanent diff.
  template_metadata {}

  filter_config {
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "HIGH"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "HIGH"
      }
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }
}
