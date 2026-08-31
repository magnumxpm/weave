"""Shared agent runtime configuration."""

import os

# Must exist on the backend actually in use: Agent Engine runs on Vertex, which
# serves a different catalogue than AI Studio. Confirm the name resolves there
# before deploying -- a model the backend does not serve fails every meeting at
# "no final response", not at startup. WEAVE_MODEL overrides it per deployment.
MODEL_NAME = os.environ.get("WEAVE_MODEL", "gemini-3.5-flash")
