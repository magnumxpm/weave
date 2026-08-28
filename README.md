# Weave

Weave extracts commitment-verified action items and structured meeting summaries from Google Meet
transcripts, enriches each owner's items only with context that owner may see, and renders one
review card per owner. Summaries retain transcript-grounded topics, decisions, implementation
notes, and reproduction steps for later meeting-aware retrieval.
There is no write path into work systems: the worst case of prompt injection is a bad
suggestion on a card a human reviews. Firestore writes are limited to pipeline state,
onboarding preferences, owner-visible action-item history, and human-confirmed lifecycle
changes to Weave's own derived commitments.

The local foundation (contracts, two-phase agents, context framework, delivery contract) runs
with no cloud dependencies. The deployed pipeline — Pub/Sub → Cloud Run → Agent Engine →
Firestore → per-owner delivery — is described in `infra/SETUP.md`, which is the repeatable
procedure for standing it up on a fresh GCP project.

Workspace users onboard themselves by adding the internal Weave app in Google Chat. That
install records their DM and provisions their per-user Meet transcript subscription; no
manual Terraform allowlist is involved. The same DM is a conversational commitment copilot:
it reconciles recurring mentions across meetings, orders work by deadline, staleness, and
blocking impact, and lets the owner explicitly mark a Weave commitment complete. It never
closes an external Google Task or writes to Drive.

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

## Future scope

Context sources are currently Google-only: attendee-visible prior meeting action items and
summaries, owner-visible Docs/Drive files, and the owner's open Google Tasks. Delegated Google reads go through an
authenticated ingestion context broker; Agent Engine never receives domain-wide delegation.
External connectors (Jira, Confluence, and other
OAuth-capable services) are deferred but designed for: each becomes a `ContextSource`
behind the same registry, authorized per user via OAuth 2.0 authorization-code flow — one
registered app per provider, per-user refresh tokens stored in Secret Manager on a
per-service basis, with only the secret's name carried on the onboarding record and passed
through `SearchPrincipal.credential_ref`. Consent is collected through a "connect" card in
the existing Chat DM plus an OAuth callback route on the ingestion service, generalizing to
N providers without agent-side credential handling. Google Issue Tracker (Buganizer) stays
out of scope entirely: it exposes no public API.
