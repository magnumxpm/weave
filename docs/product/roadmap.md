# Where Weave goes next

This prototype establishes the hard part: commitments you can trust, scoped to one person,
ordered by something explainable, delivered into a conversation people already have. The
roadmap below builds outward from that foundation — tightening consent, widening what Weave
can draw on, adding a second surface, and moving from *after the meeting* to *during* it.

Everything here extends seams that already exist in the codebase. None of it asks the
architecture to change shape.

---

## 1 · Per-user OAuth consent

**Today.** Delegated Workspace reads are performed by a single ingestion identity holding
domain-wide delegation, always executed *as* the person whose context is being read, always
read-only, and never available to the agent runtime.

**Next.** Move that consent to each individual, granted explicitly at onboarding through an
OAuth 2.0 authorization-code flow. Each person authorises exactly the scopes they choose,
sees precisely what they have granted, and can withdraw it themselves at any moment without
an administrator in the loop.

The gain is that Weave's reach becomes the union of individual, revocable consents rather
than a capability held centrally. Every read is already executed as the individual — this
makes the *authority* for that read personal too, and makes revocation something the person
performs rather than requests.

**Why it is a small change.** The seam is already in place. `SearchPrincipal` carries a
`credential_ref`, and the context broker already resolves credentials per subject rather than
per service. Per-user refresh tokens live in Secret Manager, keyed by a name carried on the
onboarding record; only that name travels, never the secret. The install conversation gains a
*connect* card, and ingestion gains a callback route — both inside the DM that already
onboards people.

The same mechanism generalises to every provider in §4, which is why it comes first.

## 2 · Anticipatory, self-healing meeting discovery

**Today.** Each person's own Meet transcript subscription fires when their transcript is
ready. It is fast, it needs no bot in the call, and it scales per person — a scheduled sweep
keeps every subscription alive well before its 7-day expiry.

**Next.** Make discovery *anticipate* meetings and *verify* itself, by combining three
signals instead of relying on one.

**Calendar-anchored anticipation.** Reading a person's calendar tells Weave which meetings
with a Meet link are coming, who was invited, what the meeting is called, and what its agenda
says. Today the agenda arrives only as the transcript document's filename, after the fact.
Reading it beforehand unlocks three things at once:

- **Better extraction.** An agenda tells the extractor what the meeting was *for*. "Discuss
  the vendor numbers" is a strong prior for interpreting an ambiguous commitment about them.
- **Pre-staged context.** Weave can warm the relevant prior commitments and documents for the
  expected attendees before the meeting ends, so results land sooner.
- **Expected-versus-actual.** A meeting on the calendar that produced no transcript is a
  *known* state rather than silence. Weave can say "this one has no transcript" instead of
  simply having nothing — which today is indistinguishable from a tenant's explicit-consent
  transcription policy quietly suppressing it.

**Multi-signal capture.** The event subscription stays the low-latency path. A Drive change
watch on the transcript folder provides a second, independent signal, so discovery never
depends on one channel behaving perfectly. Where the platform offers space-level or
organisation-level subscriptions, they collapse the per-person fan-out into a single watch.

**A reconciliation sweep that closes its own gaps.** A periodic pass lists conference records
since a high-water mark and enqueues anything absent from the processed ledger. Because every
identifier is deterministic and every write is replay-safe, re-enqueueing is free — so the
sweep can be generous. The result is a system that heals: a missed event, a lapsed renewal or
a transient outage all resolve themselves on the next pass, with no operator action.

**Meeting identity across signals.** Anchoring on the calendar event gives a meeting one
identity no matter which route discovered it, so the same conversation stays one entity even
when three signals report it.

Together: know what to expect, catch it the moment it exists, and prove afterwards that
nothing was missed.

## 3 · Gemini Enterprise as a second surface

**Today.** Google Chat is the surface that has been exercised end to end: the install
onboards, the card delivers, the DM answers. The copilot is already a separately deployed
Agent Engine app, and it takes its principal from the platform's ADK `user_id` — nothing
about it is Chat-specific.

**Next.** Register that same engine in Gemini Enterprise, so a person can ask about their
commitments wherever they are already asking questions. The work is registration and
verification rather than code: no per-user OAuth authorisation is required, because every
Google read stays brokered by ingestion and the copilot holds no delegation.

**The one thing to prove first.** Weave answers only for a caller it can identify. The
registration documentation states that ADK agents receive the invoking user's email, but
which field carries it has not been observed on a live invocation here. Weave fails closed
either way — a non-email id leaves an empty principal and every tool returns nothing, so an
unmapped identity yields a useless agent, never a leaky one. The acceptance test is
therefore: invoke as user A and confirm A resolves; invoke as user B and ask for A's
commitments, and confirm only B-owned documents are read. Until that passes, the surface stays
unregistered and the validator stays exactly as strict as it is.

Delivery would follow the same shape it takes today — one owner-scoped record per meeting,
handed over when the person next asks — rather than a push, because a pull keeps "delivered"
meaning the same thing on both surfaces.

## 4 · An open ecosystem of context sources

**Today.** Enrichment draws on four Google Workspace sources: prior action items, meeting
summaries, Drive and Docs files, and open Google Tasks. This prototype is deliberately
Workspace-only so that the isolation model could be proven end to end on one well-understood
identity system.

**Next.** Open it to everywhere else work is described.

The extension point is already the whole design. `ContextSource` is an abstract interface
with one method; a registry binds each source to a declared authentication mode and refuses
unknown names at startup; results cross as a single `ContextMatch` contract. **Adding a
provider is one subclass and one config entry** — the framework handles fan-out, per-source
failure containment, ranking and principal scoping.

Two authentication shapes cover most of the landscape:

- **OAuth providers** — Jira, Confluence, GitHub, Linear, Notion, Slack, Salesforce,
  ServiceNow. Each is one registered app, per-user tokens from §1, and the identical consent
  flow. Weave reads a person's own issues, pages, pull requests and threads *as them*.
- **API-key and service-token providers** — internal wikis, search indexes, data catalogues,
  bespoke systems. These declare a service authentication mode, and the registry keeps them
  out of user-facing queries unless a deployment explicitly opts in, so a source that cannot
  be scoped to a person never answers a person's question by accident.

Google Issue Tracker stays out of scope: it exposes no public API.

The product effect is that a commitment stops being enriched only by meetings and files, and
starts arriving with the ticket it belongs to, the pull request that implements it, and the
page that specifies it — each still filtered to what its owner can already see.

## 5 · Live mode — Weave in the room

**Today.** Weave works after the meeting, from the transcript, with no bot in the call.

**Next.** With the explicit consent of everyone present, Weave can join the meeting itself —
and the moment it does, three new things become possible.

**Commitments confirmed while everyone is still there.** Weave notices that an assignment was
made and never answered, and can surface it at the natural moment: *"Alex, is the rollout
checklist yours?"* The single largest source of dropped work is the item nobody responded to,
and in the room that costs one sentence to fix. What is silence today — correctly recorded as
`unresolved`, and correctly dropped — becomes an explicit yes or no before anyone leaves the
call.

**Answers in the meeting, from the sources that matter.** When someone asks *"what did we
decide about the vendor numbers?"* or *"is the rollback plan signed off?"*, Weave can answer
from the same owner-scoped context that powers the copilot — prior meetings, decisions,
documents, tasks, and every connected source from §4. The question that would have cost a
follow-up meeting is resolved in the meeting.

**Notes that are already structured when the call ends.** Live capture means the summary,
the commitments and the graph are built as the conversation happens. The meeting ends and
the work is already routed.

**Consent is the feature, not the paperwork.** Live mode is available only where every
participant has agreed, it is visible in the call, and the same rules hold as everywhere else
in Weave: it reads as each person, it writes nothing into anyone's systems, and every answer
cites where it came from. A participant who has not consented means Weave does not join —
which keeps the room a place where people can speak freely, and makes the presence of the
assistant something the room chose.

---

## What will not change

Each of these extends the system without touching the properties that make it trustworthy:

- **Commitments stay verified.** A promise still requires someone to have accepted it.
- **Isolation stays structural.** Every source, however it authenticates, is queried as one
  person and returns only what that person can already reach.
- **Reads stay read-only.** More connected systems means more context, never more reach.
- **Ordering stays explainable.** Priority remains arithmetic over stated facts, and every
  recommendation still says why.
