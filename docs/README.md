<p align="center">
  <img src="weave_banner.png" alt="Weave — work doesn't get lost in meetings, it gets lost after them" width="100%">
</p>

<h1 align="center">Weave documentation</h1>

<p align="center">
  <em>Every commitment from every meeting — found, verified, and routed to the one person who owns it.</em>
</p>

---

**The three core documents:** [README](README.md) · [ARCHITECTURE](ARCHITECTURE.md) ·
[DEPLOYMENT](DEPLOYMENT.md). Everything below expands on one of them.

---

## Choose your path

<table>
<tr>
<td width="33%" valign="top">

### 🎯 Product

*What Weave is and why it matters*

- **[Overview](product/overview.md)**
  The problem, the insight, a day with Weave
- **[Features](product/features.md)**
  The complete catalogue, 67 entries
- **[Scenarios](product/scenarios.md)**
  Where it changes the day, by function
- **[Roadmap](product/roadmap.md)**
  Consent, discovery, a second surface, live mode

</td>
<td width="33%" valign="top">

### ⚙️ Engineering

*How it is built*

- **[Architecture](ARCHITECTURE.md)**
  Components, flow, the two-phase pipeline
  — also as a [designed PDF](Weave_Architecture.pdf)
- **[Security model](engineering/security.md)**
  Invariants, identity, isolation, ACLs
- **[Data model](engineering/data-model.md)**
  Collections, contracts, indexes, ids

</td>
<td width="33%" valign="top">

### 🚀 Operations

*Standing it up and running it*

- **[Deployment guide](DEPLOYMENT.md)**
  Assisted end-to-end deployment, in tiers
- **[Running Weave](operations/running.md)**
  Onboarding, health, safe change, evaluation
- **[`infra/SETUP.md`](../infra/SETUP.md)**
  The terse Terraform procedure, and every trap

</td>
</tr>
</table>

## Reading routes

| If you are… | Start here |
|---|---|
| **Evaluating Weave** | [Overview](product/overview.md) → [Scenarios](product/scenarios.md) → [Features](product/features.md) |
| **An engineer** joining the codebase | [Architecture](ARCHITECTURE.md) → [Data model](engineering/data-model.md) → [Security model](engineering/security.md) |
| **An architect** reviewing the design | [Architecture](ARCHITECTURE.md) → [Security model](engineering/security.md) → [Roadmap](product/roadmap.md) |
| **Deploying it** | [Deployment](DEPLOYMENT.md) → [`infra/SETUP.md`](../infra/SETUP.md) |
| **Operating it** day to day | [Running Weave](operations/running.md) |
| **Curious where it goes** | [Roadmap](product/roadmap.md) |

## Weave in one paragraph

A Meet transcript becomes a Workspace event on the subscribing user's own subscription. Cloud
Run screens it, reads it *as that user*, and hands it to a Vertex AI Agent Engine pipeline.
Phase one reads the whole room and keeps only the commitments somebody **explicitly accepted
or was explicitly handed** — with the turn that proves it. Phase two runs once per owner, in a
fresh session as that owner, enriching only their items with only context they can already
see: prior commitments, meeting summaries, Drive files, open Tasks. Results are stored under a
per-attendee ACL, reconciled into a personal commitment graph whose edges come from
preconditions people actually stated, and delivered as a card in that person's Google Chat
direct message — the same DM that onboarded them, and the same DM that answers *what should I
do first?* Weave reads as you, writes only its own record, and can always show you the
sentence a claim came from.

## The four ideas that make it work

| | |
|---|---|
| **A promise is not an assignment** | Only an explicit *yes* — or an explicit hand-off — becomes work. Silence stays silence, and every accepted item carries the turn that proves it. |
| **One private session per person** | Enrichment runs once per owner, as that owner, over only their items. Isolation is a property of the architecture, not an instruction in a prompt. |
| **Memory across meetings** | The same deliverable raised in three meetings is one commitment with a three-week history — not three tasks in three tools. |
| **Priority you can argue with** | Ordering is arithmetic over stated facts: deadline, blocking impact, staleness, carry-over. Every recommendation says why. |

## Repository map

```
agent/            ADK agents: extraction, enrichment, orchestrator, copilot,
                  context sources, identity resolution, deployment
shared/           weave_common — the contracts every runtime shares, plus the
                  presentation-neutral commitment view
services/
  ingestion/      Pub/Sub handler, context broker, Chat events, persistence,
                  commitment reconciliation, delivery
  chat/           Google Chat interaction endpoint and inline card actions
  subscription_manager/  Meet subscription lifecycle
infra/            OpenTofu/Terraform for the whole deployment, plus SETUP.md
eval/             Evaluation sets for extraction quality and isolation
tests/unit/       265 hermetic tests — no network, no cloud, no model calls
docs/             You are here
```

## Principles

1. **Guarantees live in code.** Every safety property is enforced in Python and pinned by a
   named test, so it holds under any input.
2. **Answer or explain.** When Weave cannot justify an answer it says so, because a confident
   silence is the one outcome a person cannot act on.
3. **Read-only, by construction.** Weave's writes are its own record. Connecting more systems
   means more context, never more reach.
4. **Contain every failure.** One person's enrichment, one embedding, one source, one
   subscription — each degrades alone, and the meeting still lands for everyone else.
