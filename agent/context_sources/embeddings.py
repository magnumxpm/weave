"""Lazy Vertex AI query embeddings for semantic context retrieval."""

from __future__ import annotations

import os

# Keep these identical to weave_ingestion.embeddings. Document and query
# vectors must use the same model and dimensionality for similarity to work.
MODEL = "gemini-embedding-001"
DIMENSIONS = 768


def embed_query(text: str) -> list[float]:
    """Embed one retrieval query without loading credentials during import."""
    if not text.strip():
        return []

    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ.get("PROJECT_ID"),
        location=os.environ.get("REGION") or os.environ.get("MODEL_ARMOR_LOCATION", "us-central1"),
    )
    response = client.models.embed_content(
        model=MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=DIMENSIONS,
        ),
    )
    embeddings = response.embeddings or []
    return list(embeddings[0].values or []) if embeddings else []
