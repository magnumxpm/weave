"""Deploy the owner-scoped ADK copilot to Agent Engine."""

from __future__ import annotations

import argparse
import os

from agent.deployment.deploy import REQUIREMENTS, WHEELS


def main() -> None:
    import google.oauth2.credentials
    import vertexai
    from vertexai import agent_engines

    from agent.copilot import build_copilot

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--location", default=os.environ.get("REGION", "us-central1"))
    parser.add_argument("--service-account", default=os.environ.get("AGENT_SA"))
    args = parser.parse_args()
    if not args.project or not args.service_account:
        parser.error("--project and --service-account (or PROJECT_ID / AGENT_SA env) required")
    if not os.environ.get("CONTEXT_BROKER_URL") or not os.environ.get("CONTEXT_BROKER_AUDIENCE"):
        parser.error("CONTEXT_BROKER_URL and CONTEXT_BROKER_AUDIENCE are required")
    if not os.environ.get("WORKSPACE_TIMEZONE"):
        parser.error("WORKSPACE_TIMEZONE is required")
    for wheel in WHEELS:
        if not os.path.exists(wheel):
            raise SystemExit(f"missing {wheel}; run `uv build --all-packages` first")

    credentials = None
    if token := os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        credentials = google.oauth2.credentials.Credentials(token=token)
    vertexai.init(
        project=args.project,
        location=args.location,
        staging_bucket=f"gs://{args.project}-adk-staging",
        credentials=credentials,
    )
    app = agent_engines.AdkApp(agent=build_copilot(), app_name="weave_commitment_copilot")
    remote = agent_engines.create(
        app,
        display_name="weave-copilot",
        description="Owner-scoped conversational copilot over Weave commitments",
        requirements=REQUIREMENTS,
        extra_packages=WHEELS,
        service_account=args.service_account,
        env_vars={
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "PROJECT_ID": args.project,
            "WEAVE_MODEL": os.environ.get("WEAVE_MODEL", "gemini-3.5-flash"),
            "MODEL_ARMOR_OUTPUT_TEMPLATE": (
                f"projects/{args.project}/locations/{args.location}/templates/agent-output"
            ),
            "MODEL_ARMOR_LOCATION": args.location,
            "CONTEXT_BROKER_URL": os.environ["CONTEXT_BROKER_URL"],
            "CONTEXT_BROKER_AUDIENCE": os.environ["CONTEXT_BROKER_AUDIENCE"],
            "WORKSPACE_TIMEZONE": os.environ["WORKSPACE_TIMEZONE"],
        },
    )
    print(f"COPILOT_ENGINE_ID={remote.resource_name}")


if __name__ == "__main__":
    main()
