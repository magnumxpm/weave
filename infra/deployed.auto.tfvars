# Current deployment of weave-506221. Overriding these on the command line is
# what a rollout is; committing them keeps `tofu plan` honest between rollouts.
create_cloud_run = true
agent_engine_id  = "projects/884578202776/locations/us-central1/reasoningEngines/5959240322604072960"
image_tag        = "07e465a"

create_subscription_manager    = true
subscription_manager_image_tag = "e24ba2e"

# Numeric Cloud Identity ids, not emails (see SETUP.md §9).
onboarded_users = ["112655489411114378906"] # me@pmukherjee.dev

artifact_source = "live"
delivery_mode   = "chat"
admin_subject   = "me@pmukherjee.dev" # directory lookups only, not Meet reads
