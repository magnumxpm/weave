# Current deployment of weave-506221. Overriding these on the command line is
# what a rollout is; committing them keeps `tofu plan` honest between rollouts.
create_cloud_run = true
agent_engine_id  = "projects/884578202776/locations/us-central1/reasoningEngines/5959240322604072960"
image_tag        = "e695c5f"

create_subscription_manager    = true
subscription_manager_image_tag = "e695c5f"

artifact_source = "live"
delivery_mode   = "chat"
admin_subject   = "me@pmukherjee.dev" # directory lookups only, not Meet reads
