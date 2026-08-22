# Weave

Weave extracts commitment-verified action items from Google Meet transcripts, enriches each
owner's items only with context that owner may see, and renders one review card per owner.
Stages A and B provide the local contracts, agents, context framework, demo, and delivery
interfaces; they contain no production write path or GCP deployment.

```bash
uv python install 3.12
make install
make lint
make test
```

LLM checks are intentionally separate from hermetic unit tests. Set `GOOGLE_API_KEY` (or the
documented Vertex variables in `.env.example`) before running `make eval` or `make demo`.
