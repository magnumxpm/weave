.PHONY: install lint test eval web demo onboard build-subscription-image infra-init infra-plan infra-pass1 infra-pass2

TRANSCRIPT ?= samples/standup.txt
EVAL_DELAY_SECONDS ?= 25
TF ?= tofu

install:
	uv sync --all-packages

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest tests/unit -q

eval:
	@if [ -z "$$GOOGLE_API_KEY" ] && { [ "$$GOOGLE_GENAI_USE_VERTEXAI" != "1" ] || [ -z "$$GOOGLE_CLOUD_PROJECT" ] || [ -z "$$GOOGLE_CLOUD_LOCATION" ]; }; then \
		echo "Set GOOGLE_API_KEY or configure Vertex ADC (GOOGLE_GENAI_USE_VERTEXAI=1, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION)."; \
		exit 2; \
	fi
	uv run python scripts/run_evals.py --delay-seconds "$(EVAL_DELAY_SECONDS)"

web:
	uv run adk web .

demo:
	uv run python scripts/demo.py --transcript "$(TRANSCRIPT)"

# Emergency/manual path. Normal onboarding happens when a user adds the Chat app.
onboard:
	@test -n "$(EMAIL)" || (echo "EMAIL is required" && exit 1)
	@test -n "$(USER_ID)" || (echo "USER_ID is required" && exit 1)
	uv run python scripts/onboard.py --email "$(EMAIL)" --user-id "$(USER_ID)" \
	  $(if $(DM_SPACE),--dm-space "$(DM_SPACE)",) $(if $(PROJECT_ID),--project "$(PROJECT_ID)",)

IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
PROJECT_ID ?= $(shell cd infra && $(TF) output -raw project_id 2>/dev/null)
REGION ?= us-central1

build-image:
	gcloud builds submit --project=$(PROJECT_ID) \
	  --config=services/ingestion/cloudbuild.yaml \
	  --substitutions=_IMAGE=$(REGION)-docker.pkg.dev/$(PROJECT_ID)/weave/ingestion:$(IMAGE_TAG) \
	  --gcs-source-staging-dir=gs://$(PROJECT_ID)-adk-staging/cloudbuild \
	  --service-account=projects/$(PROJECT_ID)/serviceAccounts/weave-build-sa@$(PROJECT_ID).iam.gserviceaccount.com .

build-subscription-image:
	gcloud builds submit --project=$(PROJECT_ID) \
	  --config=services/subscription_manager/cloudbuild.yaml \
	  --substitutions=_IMAGE=$(REGION)-docker.pkg.dev/$(PROJECT_ID)/weave/subscription-manager:$(IMAGE_TAG) \
	  --gcs-source-staging-dir=gs://$(PROJECT_ID)-adk-staging/cloudbuild \
	  --service-account=projects/$(PROJECT_ID)/serviceAccounts/weave-build-sa@$(PROJECT_ID).iam.gserviceaccount.com .

deploy-agent:
	rm -rf dist && uv build --all-packages
	uv run python agent/deployment/deploy.py

infra-init:
	cd infra && $(TF) init

infra-plan:
	cd infra && $(TF) plan

infra-pass1:
	cd infra && $(TF) apply

infra-pass2:
	@test -n "$(AGENT_ENGINE_ID)" || (echo "AGENT_ENGINE_ID is required" && exit 1)
	@test -n "$(IMAGE_TAG)" || (echo "IMAGE_TAG is required" && exit 1)
	cd infra && $(TF) apply -var create_cloud_run=true -var agent_engine_id=$(AGENT_ENGINE_ID) -var image_tag=$(IMAGE_TAG)
