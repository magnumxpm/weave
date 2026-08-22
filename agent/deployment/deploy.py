"""Deploy the Weave two-phase pipeline to Vertex AI Agent Engine.

Deploys the orchestrator (never the bare extraction root_agent) as a custom
class whose only operation is `query`. The ingestion service must call the
same method name; both sides import QUERY_METHOD to keep them honest.

Run via `make deploy-agent`, which rebuilds the workspace wheels first.
"""

from __future__ import annotations

import argparse
import os

QUERY_METHOD = "query"

WHEELS = [
    "./dist/weave_common-0.1.0-py3-none-any.whl",
    "./dist/weave_agent-0.1.0-py3-none-any.whl",
]

# Exact pins mirrored from uv.lock; the wheels carry the same constraints.
REQUIREMENTS = [
    *WHEELS,
    "google-adk==2.5.0",
    "google-cloud-aiplatform[agent_engines]==1.165.1",
    "google-cloud-firestore==2.28.1",
    "google-cloud-modelarmor==0.7.1",
    "cloudpickle==3.1.2",
    "pydantic==2.13.4",
    "pyyaml==6.0.3",
]


class WeavePipeline:
    """Custom Agent Engine template; all agent imports stay inside methods so
    cloudpickle ships only this shell and the runtime imports from the wheels."""

    def set_up(self) -> None:
        from agent.agents.enrichment import make_run_enrichment
        from agent.callbacks import make_screen_output_callback

        callback = None
        template = os.environ.get("MODEL_ARMOR_OUTPUT_TEMPLATE")
        if template:
            callback = make_screen_output_callback(
                template, os.environ.get("MODEL_ARMOR_LOCATION", "us-central1")
            )
        self._enrich = make_run_enrichment(after_model_callback=callback)

    def query(self, *, request: dict) -> dict:
        from weave_common import PipelineRequest

        from agent.agents.orchestrator import run_pipeline

        parsed = PipelineRequest.model_validate(request)
        return run_pipeline(parsed, enrich=self._enrich).model_dump(mode="json")


def main() -> None:
    import google.oauth2.credentials
    import vertexai
    from vertexai import agent_engines

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--location", default=os.environ.get("REGION", "us-central1"))
    parser.add_argument("--service-account", default=os.environ.get("AGENT_SA"))
    args = parser.parse_args()
    if not args.project or not args.service_account:
        parser.error("--project and --service-account (or PROJECT_ID / AGENT_SA env) required")

    for wheel in WHEELS:
        if not os.path.exists(wheel):
            raise SystemExit(f"missing {wheel}; run `uv build --all-packages` first")

    # Terraform-style token override so deploys work before ADC is fixed up.
    credentials = None
    if token := os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        credentials = google.oauth2.credentials.Credentials(token=token)

    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=f"gs://{args.project}-adk-staging",
        credentials=credentials,
    )

    remote = agent_engines.create(
        WeavePipeline(),
        display_name="weave-pipeline",
        requirements=REQUIREMENTS,
        extra_packages=WHEELS,
        service_account=args.service_account,
        env_vars={
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "WEAVE_MODEL": os.environ.get("WEAVE_MODEL", "gemini-2.5-flash"),
            "MODEL_ARMOR_OUTPUT_TEMPLATE": (
                f"projects/{args.project}/locations/{args.location}/templates/agent-output"
            ),
            "MODEL_ARMOR_LOCATION": args.location,
        },
    )
    print(f"AGENT_ENGINE_ID={remote.resource_name}")


if __name__ == "__main__":
    main()
