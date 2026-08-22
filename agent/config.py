"""Shared agent runtime configuration."""

import os

# Must exist on the backend actually in use. Agent Engine runs on Vertex, where
# this project serves gemini-2.5-*; the 3.x names are AI Studio only today, so a
# newer model is an env override rather than an edit here.
MODEL_NAME = os.environ.get("WEAVE_MODEL", "gemini-2.5-flash")
