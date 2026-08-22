# Weave

Weave extracts commitment-verified action items from Google Meet transcripts, enriches each
owner's items only with context that owner may see, and renders one review card per owner.
There is no write path anywhere: the worst case of prompt injection is a bad suggestion on a
card a human reviews.

The local foundation (contracts, two-phase agents, context framework, delivery contract) runs
with no cloud dependencies. The deployed pipeline — Pub/Sub → Cloud Run → Agent Engine →
Firestore → per-owner delivery — is described in `infra/SETUP.md`, which is the repeatable
procedure for standing it up on a fresh GCP project.

Transcript retrieval sits behind a `MeetArtifactSource` seam with `fixture` and `live`
implementations, so the entire pipeline can be exercised end-to-end before any Google
Workspace integration exists.

```bash
uv python install 3.12
make install
make lint
make test
```

LLM checks are intentionally separate from hermetic unit tests. Set `GOOGLE_API_KEY` (or the
documented Vertex variables in `.env.example`) before running `make eval` or `make demo`.
