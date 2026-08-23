"""Lazy Vertex AI embeddings for persisted action-item documents."""

from __future__ import annotations

import os
from collections.abc import Sequence

MODEL = "gemini-embedding-001"
DIMENSIONS = 768


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed documents without requiring credentials at module import time."""
    if not texts:
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ.get("PROJECT_ID"),
        location=os.environ.get("REGION", "us-central1"),
    )
    response = client.models.embed_content(
        model=MODEL,
        contents=list(texts),
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=DIMENSIONS,
        ),
    )
    return [list(embedding.values or []) for embedding in response.embeddings or []]
