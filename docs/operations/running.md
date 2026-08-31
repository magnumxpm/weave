# Running Weave

How Weave behaves once it is live: how people join, what it does on a normal day, how to see
that it is healthy, and how to change it safely.

## Onboarding a person

Weave is per-person. Nobody sees anything until they are enrolled, and enrolment is
self-service — there is no allowlist to maintain and no administrator in the loop.

**A user opens Chat → New chat → Weave and sends any message.** That single event:

1. records their numeric Cloud Identity id and their exact DM space in `onboarded_users`;
2. submits an immediate subscription-manager sweep, so their Meet transcript subscription is
   provisioned without waiting for the schedule;
3. answers with a welcome card explaining that action items will appear here, and that they
   can ask about their commitments.

Availability is an administrator decision; installation is the user's. The install is only an
opt-in signal — domain-wide delegation remains the authority for reading Meet data, and adding
the app grants no new permissions.

**Leaving** is symmetrical in design: an offboarding request tombstones the record, and the
next sweep removes the person's Meet subscription *before* removing the record itself, so
nobody is ever left with a subscription for a system they have left. Note that Chat does not
reliably signal removal for direct messages, so a departing user may need to be offboarded
through the ledger rather than by uninstalling.

For recovery — a legacy record with no `dm_space`, or a user who must be enrolled by hand — a
person can be seeded directly:

```bash
make onboard EMAIL=user@yourdomain USER_ID=<numeric-id> DM_SPACE=spaces/<id> \
  PROJECT_ID="$PROJECT_ID"
```

## What happens on a normal day

Nobody does anything. Meetings end, transcripts become available, and each one is screened,
extracted, enriched per owner, reconciled into the commitment graph, and delivered as a Chat
card — usually within a minute of the transcript appearing.

A scheduled sweep keeps every enrolled person's Meet subscription alive, renewing well before
expiry so a missed run never silently drops someone's meetings.

The things worth knowing about steady state:

- **A meeting is processed exactly once.** Duplicates are recognised and ignored.
- **One person's problem is only theirs.** A failure enriching one owner's items produces one
  plain bundle for that owner; everyone else's results are unaffected. The same is true of
  delivery, and of a subscription sweep.
- **Nothing is lost to a partial outage.** If semantic indexing is unavailable, history is
  still written and search falls back to lexical matching. If graph reconciliation fails, it
  is recorded and delivery proceeds anyway.
- **A meeting that produces nothing is a real answer.** If nobody committed to anything, the
  meeting summary is still written and no action items are invented.

## Talking to Weave

Everything happens in the same direct message that onboarded the user:

- *"what needs my attention?"* → the top few, each with a recommendation and a reason.
- *"list everything that's still open"* → the inventory, not advice.
- *"what did we decide about the migration?"* → answered from the meeting summaries they can
  see.
- *"what did I pick up today?"* → resolved in `WORKSPACE_TIMEZONE`, not UTC.
- **Mark done** on a card, or *"the migration guide is done"* followed by an explicit
  confirmation → the commitment closes. Nothing external is touched.

One DM keeps its history across turns, because the copilot session id is derived from the
space. A copilot failure answers with an apology rather than silence, and is never retried
into a duplicate answer.

### Working on prompts and tools locally

```bash
make web PROJECT_ID="$PROJECT_ID" WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"
```

Serves both agents at <http://127.0.0.1:8000> against the deployed Firestore, so prompt and
tool changes can be tried without a redeploy.

In the browser UI the extraction agent works normally, but every copilot tool returns nothing.
That is correct behaviour, not a fault: the development UI supplies a fixed placeholder rather
than an email, and Weave refuses to guess whose commitments to show. To exercise the copilot
as a real person, call the server's API with an address:

```bash
U=me@example.com; S=$RANDOM
curl -s -X POST "http://127.0.0.1:8000/apps/agent.copilot/users/$U/sessions/$S" \
  -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8000/run -H 'Content-Type: application/json' -d "{
  \"app_name\":\"agent.copilot\", \"user_id\":\"$U\", \"session_id\":\"$S\",
  \"new_message\":{\"role\":\"user\",\"parts\":[{\"text\":\"what do I need to do?\"}]}}"
```

The faithful test of what is actually deployed is asking in Chat as two different users.

## Health

```bash
# Services and the images they are running
gcloud run services list --region="$REGION" \
  --format="table(metadata.name, status.url, status.latestReadyRevisionName)"

# Search indexes — all eight must be READY
gcloud firestore indexes composite list --project="$PROJECT_ID"

# The pipeline refuses anonymous callers
for p in pubsub-push context/search chat-events; do
  curl -s -o /dev/null -w "$p %{http_code}\n" -X POST "<INGESTION_URL>/$p"   # each 403
done

# The Chat endpoint refuses an unsigned caller
curl -s -o /dev/null -w '%{http_code}\n' -X POST "<CHAT_URL>/"               # 401
```

**Signals worth watching.** `meeting processed` is the success line, carrying the owner count
and how many candidate items were dropped as unverified. Investigate `processing failed`,
`commitment judgment failed`, `commitment vector lookup failed`,
`dropping out-of-scope enriched item`, `context source failed`, and `unresolved participant
dropped`.

Two log lines deserve special attention:

- **`chat event ignored`** — the payload did not parse as an install, a message, or a click.
  It carries the event type, the payload keys and the space type precisely so a failed
  onboarding is not acked into silence. If users report that adding Weave did nothing, look
  here first.
- **`Chat interaction received`** with its `envelope_dialect` — this is how you find out which
  Chat envelope your app actually produces, rather than assuming.

## Changing things safely

Deploy in dependency order: **ingestion first, then the agents.** The pipeline agent and
ingestion share a data contract, and ingestion must be able to read what the agent produces
before the agent starts producing it. Deploying a schema-widening agent first fails every
meeting at `PipelineResult.model_validate`.

```bash
make lint && make test          # 265 hermetic tests; no cloud needed

make build-image
make infra-pass2 AGENT_ENGINE_ID=<id> IMAGE_TAG=$(git rev-parse --short HEAD) \
  WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"

make deploy-agent
make deploy-copilot WORKSPACE_TIMEZONE="$WORKSPACE_TIMEZONE"
```

`infra/deployed.auto.tfvars` records the current rollout, which keeps `tofu plan` honest
between changes.

Most capabilities are independent switches, so each can be turned on — or rolled back —
without touching the others: `artifact_source`, `delivery_mode`, `create_cloud_run`,
`create_subscription_manager`, `manage_domain_restricted_sharing`, and `copilot_engine_id`.

### Adding a search capability

Semantic search and the commitment graph each depend on a Firestore index, and both follow
the same shape:

1. Apply the index and wait for it to report `READY`.
2. Deploy ingestion, so new writes carry the new fields.
3. Backfill the existing records.
4. Deploy the agents.

```bash
make backfill-embeddings PROJECT_ID="$PROJECT_ID"    # semantic history
make backfill-commitments PROJECT_ID="$PROJECT_ID"   # commitment graph
```

Both are safe to re-run.

> **Verify a graph backfill properly.** "Commitments appeared" is not evidence of success —
> a total failure produces exactly that, one commitment per mention. Three independent
> degradations all end there: the reconcile model call failing, the `commitments_vector` index
> not yet `READY`, and embeddings not surviving the read. Check instead that at least one
> commitment has `mention_count > 1` whose mentions name **different meetings**, and that the
> logs contain no `commitment judgment failed` or `commitment vector lookup failed`. Then
> spot-check quality: the same deliverable across meetings should merge, and the same *topic*
> alone must not.

> **Verify an embedding backfill properly.** A record with no embedding is invisible to
> semantic search — the lexical fallback keeps search working, which is exactly what makes the
> gap easy to miss. Confirm no action-item record is left without one.

## Quality evaluation

Model-dependent checks are kept separate from the unit tests, which are hermetic and run
everywhere:

```bash
make test    # 265 tests: no network, no cloud credentials, no model calls
make eval    # extraction quality and isolation, judged against fixed cases
```

`eval/extraction_cases.json` covers extraction quality — does it find the real commitments,
and does it correctly refuse the ones nobody accepted. `eval/leakage_cases.json` covers
isolation. Both are judged at a 0.8 threshold.

## Scale and cost

- Cloud Run services scale to zero; nothing runs between meetings.
- The unit of scale is **one Meet subscription per person**, renewed automatically well
  before its 7-day expiry.
- Each meeting costs one extraction pass, one enrichment pass *per owner with commitments*,
  and one reconciliation judgement per new mention. A meeting where nobody committed to
  anything costs a single extraction.
- Storage per meeting is one summary, one record per action item, and one commitment per
  distinct deliverable plus its mentions.

## Common questions

**Someone added Weave and nothing happened.** Check `chat event ignored` in the ingestion
logs, and confirm the Chat app's Configuration page names a publisher that `chat_events.tf`
actually granted. Both failures are silent from the user's side.

**A card arrived with a header and no items.** Enrichment echoed the items back inexactly and
the fingerprint gate dropped them. The orchestrator falls back to an unenriched bundle; look
for `dropping out-of-scope enriched item` warnings.

**A button click reports that the commitment is gone.** The mention id is built from the bare
conference id and a zero-based index, while the card counts from one. If a click cannot find
its commitment, that mapping — or a reconciliation that never ran — is where to look.

**Someone says Weave has nothing for them.** Check they are enrolled and that their principal
resolves — Weave never invents an empty answer, so "nothing" is either genuinely nothing or a
refusal.

**A meeting produced no action items.** Expected when nobody explicitly accepted anything.
The `meeting processed` line reports how many candidates were dropped as unverified, which
distinguishes "found nothing" from "verified nothing".

**An external guest did not get their items.** Guests outside the directory cannot be
resolved to an owner. They are skipped individually — the meeting still processes for everyone
else. If *every* participant fails, the meeting fails loudly instead, because that is a broken
directory rather than a room of guests.

**A meeting produced no transcript at all.** If the tenant requires explicit consent for
transcription, a transcript exists only where a participant accepted the prompt. There is
nothing for Weave to read.

**Semantic search feels shallow.** Confirm the indexes report `READY` and that the backfill
has run — search silently degrades to lexical matching rather than failing.

Longer-tail failures, each of which cost a debugging cycle, are tabulated at the end of
`infra/SETUP.md`.
