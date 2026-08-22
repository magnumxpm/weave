# PLAN — Chat-app self-serve onboarding + delivery failure isolation

Users onboard by installing the Weave Chat app themselves (starting a DM with
it). That install event becomes the onboarding signal: it writes the user to
Firestore, stores their DM space, and provisions their Meet transcript
subscription. Delivery failures and non-onboarded participants never fail a
meeting.

Implement in order: **Part A ships alone first** (it fixes a live defect and
unblocks the real-meeting test); Part B builds on it.

## Decisions already made (do not relitigate)

1. **Internal app only.** No Marketplace publication, no OAuth verification, no
   new user-facing permissions. DWD remains the sole data authority; the
   install is an opt-in *signal*, not a grant. Say so in code comments where
   the two could be confused.
2. **Onboarding source of truth** is Firestore collection `onboarded_users`,
   doc id = numeric Cloud Identity id. The `onboarded_users` Terraform
   variable and `ONBOARDED_USERS` env var are removed at the end of Part B.
3. **Delivery failures return 200.** The idempotency lease makes a Pub/Sub
   retry a no-op, so 500 on a failed DM only pollutes the DLQ. Outcomes are
   recorded per owner instead.
4. **Non-onboarded owners still get their `action_items` written** with
   `visible_to` as today — the ACL already restricts reads to meeting
   attendees, and it means their history is present if they onboard later.
   Only *delivery* is gated on onboarding.
5. **The stored DM space is the delivery target.** `ADDED_TO_SPACE` hands us
   `space.name`; using it directly removes both the `findDirectMessage` 404
   class and the per-delivery Directory id lookup. `findDirectMessage`
   remains only as a fallback for docs missing a space.

---

## Part A — Delivery isolation + onboarding gate (no new infra)

### A1. Ledger: onboarding reads and delivery outcomes

`services/ingestion/weave_ingestion/firestore_client.py`:

- `ONBOARDED = "onboarded_users"`. Doc shape (written by Part B, seeded by
  `make onboard` until then):
  `{email, user_id, dm_space, onboarded_at, status: "active" | "offboarding"}`.
- `MeetingLedger.onboarded_by_email() -> dict[str, OnboardedUser]` — one
  fetch per meeting, keyed by casefolded email. `OnboardedUser` is a small
  frozen pydantic model in this module (`user_id`, `email`, `dm_space`) — not
  in `weave_common` (it is ingestion-internal, and weave_common stays free of
  storage concerns).
- `MeetingLedger.record_outcome(conference_id, owner_email, outcome: str)` —
  merge-writes into `processed_meetings/{id}.deliveries.{email}`. Firestore
  field paths reject `.` inside keys, so store the map with ``-free
  sanitised keys: replace `.` with `,` (comment why).

### A2. Handler: per-owner delivery loop

`main.py` — replace the current all-or-nothing block:

```python
onboarded = ledger.onboarded_by_email()
outcomes: dict[str, str] = {}
for bundle in result.bundles:
    owner = bundle.owner_email
    target = onboarded.get(owner.casefold())
    if target is None:
        outcomes[owner] = "skipped_not_onboarded"
        continue
    try:
        deliverer.deliver(owner, bundle, target)
        outcomes[owner] = "delivered"
    except Exception:
        logger.exception("delivery failed", extra={"owner_email": owner, ...})
        outcomes[owner] = "delivery_failed"
# action_items written exactly as today, for every bundle, before status:
status = "delivered_partial" if "delivery_failed" in outcomes.values() else "delivered"
```

- `ledger.mark(...)` gains the outcomes map (single write together with
  status; drop `record_outcome` if this proves sufficient — prefer one write).
- Log one summary line: counts per outcome + conference id.
- Exceptions from `fetch`/screen/agent keep today's behaviour (mark `failed`,
  500): those are retryable; deliveries are not.

### A3. Deliverer signature

`delivery/base.py`: `deliver(owner_email, bundle, target: OnboardedUser | None = None)`.

- `ChatDeliverer.deliver`: if `target and target.dm_space` → skip
  `findDirectMessage`, `messages().create(parent=target.dm_space, ...)`.
  Else current lookup path. Keep the owner-mismatch guard.
- `LogDeliverer` / `GeminiEnterpriseDeliverer`: accept and ignore `target`.

### A4. Bootstrap tooling (until Part B exists)

`scripts/onboard.py` + `make onboard EMAIL=... USER_ID=... [DM_SPACE=...]`:
writes the `onboarded_users` doc via ADC. Document in the Makefile help that
this is a stopgap replaced by self-install.

### A5. Tests (`tests/unit/test_ingestion_handler.py`, extend fakes)

- `test_not_onboarded_owner_is_skipped_but_items_still_written`
- `test_one_owner_delivery_failure_does_not_block_the_other` — two bundles,
  first deliverer call raises; second delivered; status `delivered_partial`;
  HTTP 200; outcomes map records both.
- `test_all_deliveries_ok_marks_delivered`
- `test_delivery_uses_stored_dm_space` (in `test_delivery.py`): target with
  `dm_space` → no `findDirectMessage` call, `create` parented on the space.
- `FakeLedger` gains `onboarded` dict + recorded outcomes.

✅ **Part A done when:** all tests green; deployed with the two test users
seeded via `make onboard`; a fixture smoke event yields
`status=delivered_partial`-free happy path; a live two-user meeting delivers
to onboarded users only and a deliberately broken owner (bad space id) leaves
the other's DM intact.

---

## Part B — Self-serve onboarding via the Chat app

### B1. Infra (`infra/chat_events.tf`, additions to `iam.tf`, `variables.tf`)

- `google_pubsub_topic.chat_events` (`chat-events`).
- Chat's push identity `chat-api-push@system.gserviceaccount.com` →
  `roles/pubsub.publisher` on that topic (DRS exception already covers
  out-of-org grants).
- Push subscription `chat-events-push` → `{ingestion_url}/chat-events`, same
  push SA, same audience, `ack_deadline_seconds=60` (no long work here), DLQ
  `meet-artifacts-dlq` reused, 5 attempts.
- `weave-subscriptions-sa` → `roles/datastore.user` (job now reads/writes
  `onboarded_users`).
- `weave-ingestion-sa` → `roles/run.invoker` **on the subscription-manager
  job only** (handler triggers a sweep after onboarding; job-level
  `google_cloud_run_v2_job_iam_member`, not project-level).

### B2. Chat app console change (manual, document in SETUP.md §6c)

Chat API → Configuration: enable interactive features, Connection settings →
**Cloud Pub/Sub**, topic `projects/<project>/topics/chat-events`. Visibility
unchanged (domain only). Record: users onboard by Chat → New chat → "Weave".

### B3. `/chat-events` handler (`main.py` + new `chat_events.py`)

`weave_ingestion/chat_events.py` (pure parsing, no I/O):

- `parse_chat_event(decoded_json) -> ChatEvent | None` where `ChatEvent` is
  `{kind: "added" | "removed", user_id, email: str | None, space_name}`.
  - `ADDED_TO_SPACE` with `space.singleUserBotDm`/`spaceType == "DIRECT_MESSAGE"`
    → `added`. Group-space adds are ignored (v1 delivers to DMs only).
  - `REMOVED_FROM_SPACE` → `removed`.
  - Anything else (`MESSAGE`, `CARD_CLICKED`, unknown) → `None`, acked. A
    user messaging the app is not an error; ack and ignore.
  - `user.name` is `users/{numeric_id}` — strip the prefix. Take
    `user.email` when present; else `None` (resolved later via Directory
    with `admin_subject`).
- Route in `create_app`: `POST /chat-events`, same OIDC verifier as
  `/pubsub-push`. Flow: verify → parse → on `added`: resolve email if
  missing → write `onboarded_users/{user_id}` (idempotent set) → trigger the
  subscription job → 200 → optionally send a welcome card into the space
  (nice-to-have; do it last, failures ignored). On `removed`: set
  `status="offboarding"` on the doc (tombstone — the job needs it to find
  and delete the Meet subscription, since deletion requires impersonating
  the user) → trigger job → 200. Handler exceptions → 500 (Chat/PubSub
  retries; all writes idempotent).
- Job trigger: `run_v2` REST `:run` call behind an injectable
  `trigger_sweep: Callable[[], None]`; never blocks the response on job
  completion. **Do not import `weave_subscriptions` into ingestion** — it
  depends on `weave_ingestion` and would cycle; the job boundary is the
  decoupling.

### B4. Subscription manager reads Firestore

`services/subscription_manager/weave_subscriptions/main.py`:

- Drop `ONBOARDED_USERS`. Read `onboarded_users` collection (injectable
  client, reuse the Firestore fake).
- `status == "active"` → `ensure_subscription` (unchanged).
- `status == "offboarding"` → `delete_subscription(service, name)` (new in
  `manager.py`: list by event type, delete each), then delete the doc.
  Failure leaves the tombstone for the next sweep.
- Exit non-zero only on failures, as today.

### B5. Cleanup

- Remove `onboarded_users` + `ONBOARDED_USERS` from `variables.tf`,
  `subscription_manager.tf`, `deployed.auto.tfvars`.
- SETUP.md: §6b (org-wide install) demoted to "optional alternative"; §9
  rewritten around self-install; traps table adds the group-space-ignored and
  tombstone rows. `make onboard` documented as emergency/manual path.

### B6. Tests

- `tests/unit/test_chat_events.py`: added-DM parses; group add ignored;
  removed parses; MESSAGE/garbage → `None`; numeric id extracted from
  `users/123`.
- Handler tests: added event writes doc + triggers sweep (recorded fake);
  removed sets tombstone + triggers; bad OIDC → 403; unknown event → 200,
  no writes; missing email resolves via injected directory fake.
- `test_subscription_manager.py`: firestore-fake-driven sweep — active →
  ensured; offboarding → subscription deleted + doc deleted; delete failure
  keeps tombstone.

✅ **Part B done when:** a fresh test user DMs the app in Chat and, with no
human action: their `onboarded_users` doc exists with `dm_space`, the job run
shows `created` for them, and their next transcribed meeting produces a card
in that same DM. Removing the app deletes the subscription on the next sweep
and their meetings stop producing events.

---

## Sequencing & effort

| Step | Depends on | Size |
|---|---|---|
| A1–A5 delivery isolation + gate | — | ~half day, ship immediately |
| B1 infra + B2 console | A | small |
| B3 handler | B1 | the main chunk |
| B4 job + B5 cleanup | B3 | small |

Meanwhile, the live meeting test needs none of this: seed both users with
`make onboard` (Part A) — or today, have each user DM the app once so
`findDirectMessage` finds a space.

## Traps

- Chat event `user.email` is not guaranteed → always fall back to Directory.
- Group-space `ADDED_TO_SPACE` must not onboard whole rooms — DM-only check.
- Deleting a Meet subscription requires impersonating its user → offboarding
  must tombstone, never hard-delete the doc first.
- Firestore map keys cannot contain `.` → sanitise email keys in outcomes.
- The push subscription retries `/chat-events` on 500: every write there must
  be idempotent (`set`, not `create`).
- Do not import `weave_subscriptions` from `weave_ingestion` (dependency
  cycle); the Cloud Run job invocation is the boundary.
