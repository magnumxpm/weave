# Current deployment of weave-506221. Overriding these on the command line is
# what a rollout is; committing them keeps `tofu plan` honest between rollouts.
create_cloud_run  = true
agent_engine_id   = "projects/884578202776/locations/us-central1/reasoningEngines/8987361316196843520"
copilot_engine_id = "projects/884578202776/locations/us-central1/reasoningEngines/6586098289878237184"
image_tag         = "7f8f4c7"

create_subscription_manager    = true
subscription_manager_image_tag = "e425c98"

artifact_source = "live"
delivery_mode   = "chat"
admin_subject   = "me@pmukherjee.dev" # directory lookups only, not Meet reads

create_chat_service = true
chat_image_tag      = "8519ae4"
chat_audience       = "https://weave-chat-32iyowmc5q-uc.a.run.app"
