# How this was built: a guided walkthrough

Read this top to bottom with the code open beside it. Each part explains **the idea**, **where it lives**,
and **the trap** it avoids. Part 9 is a hands-on lab, and Part 10 has exercises. If you can do the
exercises without looking, you can explain this system in an interview.

---

## Part 0. The one-paragraph mental model

Ringg is the **mouth and ears**: it dials, listens, talks and fills in a post-call form. This service is the
**brain and memory**. It decides *who* to call *when*, gives the agent *facts and actions* during the call
(tools), and *remembers and reacts* afterwards (webhooks). Everything is wired through four doors:

| Door | Who knocks | File |
|---|---|---|
| `/leads/*`, `/visits/*`, `/campaigns/*` | the client's systems and sales team | [routes/leads.py](../app/routes/leads.py) |
| `/tools/*` | the Ringg agent, **during** a call | [routes/tools.py](../app/routes/tools.py) |
| `/webhooks/*` | Ringg **after** a call, WhatsApp when a lead replies | [routes/webhooks.py](../app/routes/webhooks.py) |
| the scheduler loop | itself, every 5 seconds | [scheduler.py](../app/scheduler.py) |

The rest is shared logic that these four doors call.

---

## Part 1. Build order (and why this order)

I didn't start with code. The order was:

1. **Research the business.** Ringg's case studies showed what customers measure (calls fully handled by the AI, qualified %, % escalated to a person). Indian presales research showed the real goal is **site visits that happen**.
2. **Design** ([AGENT_DESIGN.md](AGENT_DESIGN.md)): agents, funnel stages, cadence, guardrails. This decided *what* to build.
3. **v1, the thinnest end-to-end slice:** one agent, three tools (send brochure, book visit, project info), one webhook. It proved the Ringg ↔ service contract before investing more.
4. **Storage + scheduler.** Everything else ("call again in 30 minutes", "remind 24 hours before") needs a durable queue, so it came before any playbook.
5. **Playbooks** on top of the queue: speed-to-lead, then the visit concierge.
6. **Intelligence:** inventory, pricing, cost sheet, scoring, managers.
7. **Wiring:** routes, WhatsApp replies, campaigns, dashboard.
8. **Tests with a frozen clock.** These found a real bug (Part 4.4).
9. **Prompts + Ringg config** written *after* the tools existed, so the prompts reference real tool behaviour.
10. **Evals**: an LLM plays the agent and an LLM plays the caller, to test the prompts.

**Lesson:** build a thin vertical slice first, then the foundation everything shares (the queue), then the features.

---

## Part 2. Data: one row per lead, plus a log of everything

File: [db.py](../app/db.py). Schema at [db.py:23](../app/db.py:23).

| Table | Purpose |
|---|---|
| `leads` | one row per **phone number**: profile, requirement, score, `stage`, owner, opt-out |
| `visits` | site visits and their status (`booked → confirmed → done / no_show / rescheduled`) |
| `tasks` | the cadence queue: *do X for this phone at time T* |
| `messages` | every outbound call / WhatsApp / email, mocked or real (your audit trail) |
| `inbound` | WhatsApp messages received |
| `webhook_events` | every Ringg event, keyed for idempotency |

Three patterns worth stealing:

1. **`upsert_lead` never overwrites with blanks** ([db.py:124](../app/db.py:124)). A later call where the caller didn't mention their budget shouldn't erase the budget we already have. It drops `None` and `""` before the SQL `ON CONFLICT ... DO UPDATE`.
2. **The funnel only moves forward** ([db.py:151](../app/db.py:151)). `advance_stage` refuses to move a `visit_booked` lead back to `contacted` just because another call happened. Side stages (`nurture`, `lost`, `opted_out`) are set directly.
3. **All timestamps are UTC ISO strings**, so `run_at <= now` works as a plain string comparison in SQL. IST only appears when talking to humans.

---

## Part 3. The scheduler: the heart of the system

File: [scheduler.py](../app/scheduler.py). Read it fully; it's about 110 lines.

**The idea:** a task is *"do X for this lead at time T, **if still relevant**."* Relevance is checked **when the task runs**, not when it's scheduled, because between scheduling and running the lead may have answered, booked a visit or opted out.

How one tick works ([run_due, scheduler.py:87](../app/scheduler.py:87)):
```
for each pending task with run_at <= now:
    check_guards(task)      → raise Skip     → mark cancelled ("skipped: opted out")
    HANDLERS[kind](task)    → raise Postpone → keep pending, move run_at (e.g. to 10:00 tomorrow)
                            → return text   → mark done
                            → any exception → mark failed (the loop survives)
```

- **Guards** ([check_guards, scheduler.py:69](../app/scheduler.py:69)): opted out? stage still one where this makes sense (`stage_in`)? visit still active (`visit_status_in`)? too late to be useful (`not_after`, e.g. a reconfirm call after the visit has started is pointless)?
- **Postpone vs Skip** is the key distinction. *Outside calling hours* → Postpone (still worth doing later). *Lead already booked a visit* → Skip (no longer worth doing).
- **Handlers register themselves** with a decorator (`@handler("call")`, [scheduler.py:33](../app/scheduler.py:33)). The scheduler doesn't know what a call is. That's [cadence.py](../app/cadence.py)'s job.
- **Dedupe keys** (`tasks.dedupe_key UNIQUE` + `INSERT OR IGNORE`). Scheduling the same step twice is harmless, which makes every caller of `schedule()` safe to retry.
- **The loop** ([scheduler.py:107](../app/scheduler.py:107)) is started in FastAPI's `lifespan` ([main.py:23](../app/main.py:23)) and wrapped in try/except so one bad tick never kills it.

**Calling rules** live here too: `next_call_slot` ([scheduler.py:47](../app/scheduler.py:47)) moves any time into the 10:00–19:00 IST window, and `call_limit_reason` ([scheduler.py:59](../app/scheduler.py:59)) counts calls already placed today and this week from the `messages` table.

> **Interview line:** "I made the cadence a durable task queue with run-time guards, so the system is correct even when the world changes between scheduling and execution. And since it's in the database, a restart loses nothing."

---

## Part 4. Playbooks: when to touch, on which channel

File: [cadence.py](../app/cadence.py).

### 4.1 Speed-to-lead
[start_speed_to_lead, cadence.py:36](../app/cadence.py:36) schedules **only the first call**. When that call actually goes out, `_after_speed_call` schedules the next steps *relative to that moment*: WhatsApp at +1 min, re-dial at +30 min, +4 h, the next evening, "should I stop?" on day 2, nurture on day 7.

### 4.2 Visit concierge
[start_visit_concierge, cadence.py:61](../app/cadence.py:61) schedules four tasks around the visit time, each guarded:
- reminder (24 h before) and reconfirm call (20 h before): only while the visit is still unconfirmed (`visit_status_in: ["booked"]`). Tapping "Yes" on WhatsApp makes the call skip itself.
- location pin (2 h before) and late check (30 min after): only while the visit is booked or confirmed.

### 4.3 Handlers
[run_call, cadence.py:133](../app/cadence.py:133): window check → limit check → build the agent's custom variables (`call_context`, [cadence.py:112](../app/cadence.py:112), so the agent never asks what we already know) → `ringg.start_call` → record first-call time → schedule the next ladder step.

### 4.4 The bug the tests found
My first version scheduled the whole ladder at intake time: call at T, re-dial at T+30 min, T+4 h. A lead arriving at **22:15** got every call postponed to **10:00 next morning**, so 2–3 calls fired at the same moment. The test `test_night_lead_waits_for_calling_hours` caught it.
**Fix:** chain the steps, so each one is scheduled from when the previous call actually happened.
**Lesson:** relative schedules must be relative to *events*, not to *intent*.

---

## Part 5. Tools: how the agent acts during a call

File: [routes/tools.py](../app/routes/tools.py). Ringg config: [ringg/tools.md](../ringg/tools.md).

How Ringg API tools work: Ringg sends an HTTP request to your URL. Each body field is **Static** (fixed),
**Variable** (from call data, e.g. `call_data.id`, `custom_args.mobile_number`) or **Dynamic** (the LLM fills
it from the conversation). You choose which response keys the LLM reads.

Design rules I followed:

1. **The LLM fills only what it heard.** The phone number, call ID and name are Variables, never Dynamic, so the LLM can't send a brochure to a wrong number.
2. **Every response has a `result` sentence that tells the agent what to say or do next**, *including on failure*:
   *"The email address was not clear, ask the caller to spell it letter by letter."* The tool steers the conversation, so the prompt doesn't need a rule for every failure.
3. **The server does all money maths** ([pricing.py:65](../app/pricing.py:65)). LLMs make arithmetic mistakes, and a wrong price spoken on a recorded call is a real problem. The figure spoken and the PDF come from the same function, so they always match.
4. **Fuzzy input, strict output.** `find_project("palm grove")`, `parse_budget("70-80 L")` and `normalize_email("harsh dot shah at the rate gmail dot com")` absorb speech-to-text mess. Then the tool validates (visiting hours, closed days, valid email) and says what's wrong.
5. **Bookings are not retried** (retry 0 in Ringg), and `visits` has a UNIQUE key. A timeout can't double-book.
6. **Pre-call vs on-call:** `lead_context` runs *before* the call connects, so the first sentence can use it. On-call tools run only when the prompt allows.

Walk through [book_site_visit, tools.py:168](../app/routes/tools.py:168). It's the richest one: parse the date → validate the slot → assign a manager → insert the visit → if there was an earlier visit, mark it `rescheduled` and cancel its tasks → move the stage → stop the speed-to-lead ladder → rescore → start the concierge → send the WhatsApp confirmation → alert the manager → reply with one sentence.

---

## Part 6. Webhooks: reacting after the call

File: [routes/webhooks.py](../app/routes/webhooks.py), logic in [followup.py](../app/followup.py).

**Ringg webhook** ([webhooks.py:24](../app/routes/webhooks.py:24)):
1. Check the secret.
2. **Store the event first**, keyed `(call_id, event_type)`. A duplicate gets `200 {"duplicate": true}` and nothing else. Webhooks are delivered *at least once*, so assume they will be repeated.
3. Reply 200 immediately; process in a background task. Slow handlers cause the sender to time out and retry.
4. Only `all_processing_completed` matters: by then Ringg has the transcript **and** the analysis fields.

**handle_call_finished** ([followup.py:59](../app/followup.py:59)):
- Was this a real conversation? `completed` with zero caller turns means voicemail or silence ([followup.py:41](../app/followup.py:41)). Not connected → do nothing, because the cadence already has the next step queued.
- Opt-out → cancel everything, stop.
- Merge the analysis fields into the lead, stop the re-dial ladder, apply flow-specific outcomes (reconfirmed visit, post-visit next step), set the funnel stage, schedule any requested callback.
- **Safety net:** if the analysis says they wanted the brochure but the in-call tool didn't send it (`was_sent` check), send it now. That's why `messages` keys on `call_id + kind`.
- Rescore; alert the manager about hot leads.

**Where does the data come from?** The **Advanced Analysis fields** ([analysis_fields.json](../ringg/analysis_fields.json)). Ringg's LLM reads the transcript after the call and fills them in. The descriptions in that file are the prompt for that extraction, so write them precisely.

**WhatsApp replies** ([handle_reply, webhooks.py:60](../app/routes/webhooks.py:60)): quick-reply buttons carry a payload (`VISIT_CONFIRM:12`), so a tap maps straight to an action. A reply opens WhatsApp's 24-hour window, which is the only time free text is allowed, so it answers in plain text. Any free-text message is treated as a buying signal: the manager is alerted, and a dormant lead gets a call.

---

## Part 7. Channels and the "mock mode" trick

Files: [channels/whatsapp.py](../app/channels/whatsapp.py), [channels/email.py](../app/channels/email.py), [ringg.py](../app/ringg.py).

- **WhatsApp rule:** a business can only start a conversation with an **approved template**. So brochures use a template with a **document header** (that's how a PDF reaches someone who never messaged you), and location pins use a **location header**.
- **Mock mode:** with `DELIVERY_MODE=mock`, every channel writes the *exact request it would send* to the `messages` table instead of sending it. You get end-to-end tests and demos with zero keys, and you can inspect the real payloads. It's one `if not s.live:` per channel.
- **Ringg retries are off per call** (`call_retry_config: {retry_count: 0}`, [ringg.py:13](../app/ringg.py:13)). If Ringg *and* our cadence both re-dialled, the call limits would break. One owner per decision.

---

## Part 8. Scoring, managers, hand-off

- **Score** ([scoring.py:36](../app/scoring.py:36)): plain rules (budget fits inventory +25, timeline ≤3 months +25, visit +20…) that a sales head can read and argue with. Rules beat an ML model when you have no outcome data yet. The LLM's verdict can only *lower* it ("cold" wins), never raise it.
- **Managers** ([managers.py:28](../app/managers.py:28)): keep the existing owner; otherwise the least-loaded manager who sells that project and speaks the lead's language.
- **Warm transfer** ([prepare_transfer, tools.py:243](../app/routes/tools.py:243)): check the manager is on shift → WhatsApp them a one-line "whisper" ([leads.py:11](../app/leads.py:11)) → tell the agent to transfer. If nobody's on duty, tell the agent *not* to transfer and to book a callback instead. Transferring a call to nobody is the worst customer experience.

---

## Part 9. Hands-on lab (30 minutes)

Run each step and look at what changed.

**Terminal 1: start the server**
```bash
cd /Users/harshshah/Desktop/tagging/realestate-voice-agent && cp -n .env.example .env; uv run uvicorn app.main:app --reload
```

**Terminal 2:**

1. A lead arrives:
```bash
curl -s -X POST localhost:8000/leads/intake -H 'X-Intake-Secret: change-me-intake' -H 'content-type: application/json' -d '{"name":"Test Lead","phone":"9800012345","project":"skyline","source":"99acres"}'
```
2. See the call that was "placed" and the next step that got queued:
```bash
curl -s 'localhost:8000/admin/messages?limit=3' | python3 -m json.tool | head -40
```
```bash
curl -s 'localhost:8000/admin/tasks?limit=5' | python3 -m json.tool
```
3. Copy the `provider_id` from the voice message (e.g. `mock-abc123`), then pretend Ringg finished the call:
```bash
curl -s -X POST localhost:8000/webhooks/ringg -H 'Authorization: Bearer change-me-webhook' -H 'content-type: application/json' -d '{"event_type":"all_processing_completed","call_id":"PASTE_ID","status":"completed","user_message_count":8,"to_number":"+919800012345","custom_args_values":{"mobile_number":"+919800012345","callee_name":"Test","flow":"speed_to_lead"},"client_analysis":{"bhk_preference":"3 BHK","budget":"1.9 crore","purchase_timeline":"1 month","wants_brochure":true}}'
```
   Check `/admin/leads`: stage is `qualified`, there's a score, the pending speed-to-lead tasks are cancelled, and a brochure appears in messages.
4. Act as the agent mid-call:
```bash
curl -s -X POST localhost:8000/tools/send-cost-sheet -H 'X-Tool-Secret: change-me-tools' -H 'content-type: application/json' -d '{"call_id":"x","phone":"+919800012345","name":"Test","project":"skyline","bhk":"3 BHK","floor":14}'
```
   Open the PDF link from `/admin/messages` (swap the base URL for `localhost:8000`).
5. Send the same webhook again: the reply says `"duplicate": true`.
6. Open `localhost:8000/admin/dashboard`.

Then read a test end to end: `test_visit_booking_runs_concierge` in [test_flows.py](../tests/test_flows.py). It moves the frozen clock to T-24h, T-20h and T-2h and checks what happened at each point.

---

## Part 10. Testing approach

- **A frozen clock** ([clock.py](../app/clock.py)). Every "now" in the app goes through `clock.utcnow()`. Tests set the time to a Wednesday at 10:30 IST, so they pass whether you run them at noon or midnight. Without this, the calling-window tests would fail at night.
- **The `Env` helper** ([conftest.py](../tests/conftest.py)): `env.intake()`, `env.advance(hours=3)`, `env.tick()`, `env.call_done(...)`, `env.wa_reply(...)`. Tests read like a story of the lead's journey.
- **Unit tests for pure functions** (pricing, parsing), **flow tests for behaviour** (what happens over time).
- **Evals for the prompt** ([evals/run.py](../evals/run.py)): tests check the code; evals check the *conversation*. Claude plays the agent with the real prompt, calling the real tools in-process. Another Claude plays a persona (haggler, "stop calling", NRI investor…). A judge returns a typed `Scorecard` (structured output). The **hard checks come from the database** (was a visit actually booked?), not the judge's opinion. Use both together: LLM judgment for "was it polite and honest", database facts for "did it happen".

---

## Part 11. Exercises (do these to really learn it)

1. **Easy:** add a fourth project to `data/projects.json` + `data/inventory.json`, run `scripts/make_brochures.py`, and confirm `check_availability` suggests it for the right budget.
2. **Easy:** change the re-dial ladder to add a 5th call on day 3 at 12:00. Write the test first.
3. **Medium:** add a `send_floor_plan` tool (WhatsApp document per configuration). Follow the `send_cost_sheet` pattern: route, channel function, template, Ringg tool row, prompt line, test.
4. **Medium:** stop the agent from calling a lead whose site visit is already done: which guard and which flows?
5. **Medium:** add Meta's `X-Hub-Signature-256` verification to the WhatsApp webhook (HMAC-SHA256 of the raw body with the app secret).
6. **Hard:** push lead updates to a CRM (LeadSquared or a Google Sheet) after `handle_call_finished`. Make it idempotent and retryable using the task queue (hint: a new task kind `crm_sync`).
7. **Hard:** add a post-booking payment-reminder agent (flow `payment_due`): a new playbook, guards, analysis fields, prompt.
8. **Eval:** add a persona that speaks only Marathi, run the evals for it, and improve the prompt until it passes.

---

## Part 12. Explaining it in 60 seconds (interview)

> "Ringg handles the conversation; I built the system around it. Leads from portals and Meta ads get a call
> within a minute. A durable task queue runs the follow-ups (re-dials, WhatsApp, visit reminders) with guards
> checked at run time, so nobody gets called after they've booked or opted out, and calls respect TRAI hours
> and frequency limits. During the call the agent uses tools I built: live inventory, a personalised cost-sheet
> PDF where the server does all the maths, visit booking, and a warm transfer that only happens if a manager is
> on duty. After the call, Ringg's analysis fields update the lead and its score, and the funnel dashboard
> tracks speed to lead, connect rate and site-visit show-up. Webhooks are idempotent, everything has a mock
> mode, there are 57 tests on a frozen clock, and persona evals check the prompt itself."

Things an interviewer may push on, and your answers:
- *Why your own cadence instead of Ringg campaigns?* Cross-channel steps (WhatsApp between calls), and conditions Ringg can't see (visit status, WhatsApp taps). One owner for re-dial decisions.
- *What breaks at scale?* SQLite and the in-process loop: move to Postgres + a worker queue. The task table maps onto that directly.
- *Biggest risk?* Compliance (DND, consent, calling hours) and pricing accuracy. Hence server-side maths and enforced calling rules.
