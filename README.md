# Real-estate presales agent on Ringg AI

**An AI voice + WhatsApp sales team for Indian property developers.** It calls every new enquiry within a
minute, qualifies the buyer, quotes from live inventory, sends the brochure and a personalised cost sheet on
WhatsApp, books the site visit, makes sure the visit happens, and hands hot leads to a human sales manager.

Ringg AI runs the conversations. This service is everything around them: lead intake, the follow-up
cadence, live inventory and pricing, cost-sheet PDFs, site-visit reminders, WhatsApp replies, handoffs
to sales managers, post-call CRM updates, a funnel dashboard and persona evals.

> Built as a forward-deployed-engineering project: the conversation is the easy part; the deployment
> around it (timing, retries, limits, data, handoffs, measurement) is what makes it work for a client.

| Read this | For |
|---|---|
| [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md) | The business problem, the agent team, conversation design, guardrails, the cadence |
| [docs/HOW_IT_WAS_BUILT.md](docs/HOW_IT_WAS_BUILT.md) | A walkthrough of the code, the decisions, and the bugs found along the way |
| [ringg/](ringg/) | Agent prompts, tool definitions, analysis fields and WhatsApp templates to set up in Ringg and Meta |

## How it works in one minute
1. A lead arrives from a portal, a Meta ad or the website, and the server saves it and schedules a call.
2. The server asks Ringg to call. During the call the agent uses **tools** (HTTP endpoints on this server)
   for facts and actions: prices, availability, sending the brochure, booking the visit. The model never
   calculates a price; the server does.
3. After the call Ringg sends a **webhook** with the summary and extracted fields. The server updates the
   lead, rescores it, sends what was promised, and schedules the next step or alerts a manager.

```
 Lead sources ──▶ POST /leads/intake ──▶ cadence engine (SQLite task queue, calling window, call limits)
 (portals, Meta ads,                         │
  website, CRM import)                       ├─▶ Ringg outbound call ──▶ agent uses /tools/* mid-call
                                             ├─▶ WhatsApp templates (brochure, reminders, location pin)
                                             ▼
 Ringg webhook ──▶ /webhooks/ringg ──▶ update lead + score, move funnel stage, stop/redial,
                                        callbacks, send what was promised, alert manager
 WhatsApp replies ──▶ /webhooks/whatsapp ──▶ confirm / reschedule / stop / call me / free text
 Sales team ──▶ /visits/{id}/outcome ──▶ feedback call or no-show recovery
                /campaigns/reactivation, /campaigns/nurture
 Everyone ──▶ /admin/dashboard
```

## The agents
| Agent | When | Job | Prompt |
|---|---|---|---|
| Speed-to-lead | New enquiry, call within a minute | Qualify, recommend from live inventory, cost sheet, book visit | `ringg/agents/speed_to_lead.md` |
| Inbound hotline | Calls to ad and hoarding numbers, 24×7 | Same, for inbound callers, with source attribution | `ringg/agents/inbound_hotline.md` |
| Visit concierge | Before and after a booked visit | Reconfirm, late check, no-show recovery, reschedule | `ringg/agents/visit_concierge.md` |
| Post-visit feedback | 3 h after a visit | Capture what they liked and their objections, route to the manager | `ringg/agents/post_visit_feedback.md` |
| Reactivation | Campaign over old leads | Find who is ready again | `ringg/agents/reactivation.md` |
| WhatsApp | Any reply from a lead | Buttons → actions; free text → manager + callback | `app/routes/webhooks.py` |

## What the cadence does
- **New lead:** call at once → WhatsApp brochure 1 min later → re-dial at +30 min, +4 h, next evening →
  "should I stop?" with buttons on day 2 → nurture on day 7. Each step is scheduled from the time the
  previous call actually happened, so a lead that arrives at night doesn't get every re-dial at 10 am.
  A conversation stops the ladder.
- **Visit booked:** WhatsApp confirmation → reminder with *Yes / Reschedule* 24 h before → a reconfirm call
  if they haven't tapped → location pin + manager's name 2 h before → "on your way?" call 30 min after the slot.
- **Visit outcome:** done → feedback call; no-show → recovery call.
- **Rules everywhere:** calls only 10:00–19:00 IST, at most 3 calls a day and 6 a week per lead, no WhatsApp at
  night, an opt-out on any channel cancels everything. A call that isn't allowed now moves to the next allowed
  time instead of being dropped.

## Mid-call tools (what the agent can do)
`lead_context` (pre-call: what we know + manager), `project_info`, `check_availability` (fits budget? best
in-budget alternative), `send_cost_sheet` (personalised all-inclusive PDF on WhatsApp; the server does the maths),
`send_brochure` (WhatsApp/email; spoken emails normalised), `book_site_visit` (hours, closed days, reschedules),
`book_callback`, `prepare_transfer` (whisper summary to an on-duty manager, or "book a callback instead").

## Project layout
```
app/
  main.py            FastAPI app; starts the scheduler loop
  routes/            tools (called by Ringg mid-call), webhooks (Ringg, WhatsApp), leads, admin dashboard
  cadence.py         what happens when: re-dial ladder, visit concierge, nurture
  scheduler.py       durable task queue with calling-window and call-limit guards
  followup.py        everything after a call ends
  pricing.py         inventory, budget fit, all-inclusive cost (lakh/crore)
  costsheet.py pdf.py  personalised cost-sheet PDFs, no PDF library needed
  scoring.py managers.py leads.py  lead score, manager assignment, warm transfer
  channels/          WhatsApp Cloud API and email
data/                sample projects, inventory, managers
ringg/               agent prompts, tool specs, analysis fields, WhatsApp templates
evals/               persona evals (Claude as agent, caller and judge)
scripts/             sample brochures, a single test call, a demo replay
tests/               pytest suite with a frozen clock
```

## Run locally (mock mode, no keys needed)
Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).
```bash
uv sync
uv run python scripts/make_brochures.py   # sample PDFs; replace with real ones
cp .env.example .env
uv run uvicorn app.main:app --reload
```
In another terminal, replay a morning of leads and open the dashboard:
```bash
uv run python scripts/demo.py
```
Then open `http://localhost:8000/admin/dashboard`. Raw tables: `/admin/leads`, `/admin/tasks`, `/admin/messages`, `/admin/visits`.

## Tests
```bash
uv run pytest
```
57 tests with a frozen clock: pricing and budget parsing, intake formats, calling window and limits, the
re-dial ladder, opt-outs, idempotent webhooks, the visit concierge end to end, WhatsApp buttons, transfers,
reactivation and nurture campaigns, the funnel.

`tests/test_floor_plan.py` is an open exercise (a `send_floor_plan` tool, written test-first), so its 5
tests fail until that feature is implemented.

## Persona evals
Claude plays the speed-to-lead agent with the real prompt and tools, another Claude plays a caller
(first-time couple, NRI investor, haggler, budget mismatch, "stop calling", busy, broker, wants a human),
and a third grades the call. Hard checks come from the database (was the visit actually booked?).
```bash
uv run --group evals python -m evals.run --dry-run     # no API calls
uv run --group evals python -m evals.run               # costs API credits
```
Text stands in for audio, so this tests conversation logic and tool use, not speech recognition or latency.

## Go live
1. Expose the service over HTTPS and set `PUBLIC_BASE_URL`.
2. Ringg: create the four outbound agents and the inbound one from `ringg/agents/`, add tools, analysis fields
   and the webhook as described in [ringg/tools.md](ringg/tools.md). Put the agent IDs in `.env`.
3. WhatsApp Cloud API: submit the templates in [ringg/whatsapp_templates.md](ringg/whatsapp_templates.md); point the
   webhook at `/webhooks/whatsapp`.
4. SMTP credentials; manager list in `data/managers.json`; projects, inventory and charges in `data/`.
5. Connect lead sources to `/leads/intake` (Zapier/Make for Meta Lead Ads, portal exports, website form).
6. `DELIVERY_MODE=live`.

## Before any real calls (India)
Confirm with the client's counsel: TRAI/DLT registration and the right number series, DND/NCPR scrubbing,
consent on enquiry forms, calling hours, recording disclosure, DPDP Act retention and deletion, RERA numbers
exactly as registered, and stamp duty / GST / registration rates for the project's state.
The pricing, projects and managers in `data/` are samples.

## Production notes
- SQLite and an in-process scheduler are fine for a pilot (one instance). For scale, move to Postgres and a
  worker queue; the task table and handlers map directly onto one.
- Verify Meta's `X-Hub-Signature-256` on the WhatsApp webhook and put `/admin` behind SSO.
- Ringg's webhook retry and signing behaviour isn't documented publicly; confirm it with Ringg.
