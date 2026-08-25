# Current deployment of weave-506221. Overriding these on the command line is
# what a rollout is; committing them keeps `tofu plan` honest between rollouts.
create_cloud_run  = true
agent_engine_id   = "projects/884578202776/locations/us-central1/reasoningEngines/5096941731450978304"
copilot_engine_id = "projects/884578202776/locations/us-central1/reasoningEngines/6994799956062109696"
image_tag         = "77e9363"

create_subscription_manager    = true
subscription_manager_image_tag = "e425c98"

artifact_source = "live"
delivery_mode   = "chat"
admin_subject   = "me@pmukherjee.dev" # directory lookups only, not Meet reads

create_chat_service = true
chat_image_tag      = "77e9363"
