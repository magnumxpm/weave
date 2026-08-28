# Current deployment of weave-506221. Overriding these on the command line is
# what a rollout is; committing them keeps `tofu plan` honest between rollouts.
create_cloud_run  = true
agent_engine_id   = "projects/884578202776/locations/us-central1/reasoningEngines/8490170954209558528"
copilot_engine_id = "projects/884578202776/locations/us-central1/reasoningEngines/6969080180065173504"
image_tag         = "meeting-summaries-20260829"

create_subscription_manager    = true
subscription_manager_image_tag = "e425c98"

artifact_source = "live"
delivery_mode   = "chat"
admin_subject   = "me@pmukherjee.dev" # directory lookups only, not Meet reads

create_chat_service = true
chat_image_tag      = "8519ae4"
chat_audience       = "https://weave-chat-32iyowmc5q-uc.a.run.app"
