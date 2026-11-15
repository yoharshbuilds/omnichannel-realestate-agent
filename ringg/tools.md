# Ringg configuration

`BASE` = the service's public HTTPS URL (`PUBLIC_BASE_URL`). Every tool sends `X-Tool-Secret: <TOOL_SECRET>` as a
**Static** header, uses POST with a JSON body, and in the **Response** tab selects `result` (plus the extra
keys listed). Sources: **Static** = fixed, **Variable** = call context, **Dynamic** = the LLM fills it (on-call only).

`PHONE` below means `custom_args.mobile_number` for outbound agents and `call_data.from_number` for the inbound hotline.

## Agents
| Agent | Prompt | Env var with its Ringg agent ID | Tools |
|---|---|---|---|
| Speed-to-lead | `agents/speed_to_lead.md` | `RINGG_AGENT_SPEED_TO_LEAD` | all |
| Visit concierge | `agents/visit_concierge.md` | `RINGG_AGENT_CONCIERGE` | lead_context, book_site_visit, book_callback, send_brochure |
| Post-visit feedback | `agents/post_visit_feedback.md` | `RINGG_AGENT_FEEDBACK` | lead_context, book_site_visit, send_cost_sheet, prepare_transfer, book_callback |
| Reactivation | `agents/reactivation.md` | `RINGG_AGENT_REACTIVATION` | all |
| Inbound hotline | `agents/inbound_hotline.md` | (attached to inbound numbers in Ringg) | all |

Every agent: append `agents/_core_rules.md`, attach `knowledge_base/objection_playbook.md` and the project
brochures as a knowledge base, and add the Advanced Analysis fields from `analysis_fields.json`.

## Pre-call tool
### `lead_context`: `BASE/tools/lead-context`
| Body | Source | Value |
|---|---|---|
| `phone` | Variable | PHONE |

Response keys: `known`, `is_returning`, `stage`, `manager_name`, `manager_phone`, `manager_on_duty`.
Leave **Block call on status codes** empty (an unknown lead is still worth calling).

## On-call tools
| Tool | Path | Body (Dynamic unless noted) | Timeout / retries | Execution message |
|---|---|---|---|---|
| `project_info` | `/tools/project-info` | `project` | 5 s / 1 | "One second." |
| `check_availability` | `/tools/check-availability` | `project`, `bhk` ("2 BHK"), `budget` (as said, "1.2 crore") | 5 s / 1 | "Let me check what's available." |
| `send_cost_sheet` | `/tools/send-cost-sheet` | `call_id`=Variable `call_data.id`, `phone`=Variable PHONE, `name`=Variable `custom_args.callee_name`, `project`, `bhk`, `floor` (number, optional) | 10 s / 1 | "Working that out for you." |
| `send_brochure` | `/tools/send-brochure` | `call_id`, `phone`, `name` (Variables as above), `project`, `channel` (whatsapp / email / both), `email` | 10 s / 1 | "Sending it now." |
| `book_site_visit` | `/tools/book-site-visit` | `call_id`, `phone`, `name` (Variables), `project`, `date` (YYYY-MM-DD), `time` (HH:MM 24h), `attendees` | 10 s / **0** | "Let me block that slot." |
| `book_callback` | `/tools/book-callback` | `call_id`, `phone` (Variables), `date`, `time`, `with_person` (boolean), `reason` | 5 s / 0 | — |
| `prepare_transfer` | `/tools/prepare-transfer` | `call_id`, `phone` (Variables), `notes` ("one line on what they want") | 5 s / 1 | "Let me get someone for you." |

Bookings are never retried automatically, so a timeout can't double-book. The server also deduplicates.

## System tools
- **Transfer call** → target `@((lead_context.manager_phone))`. The prompt only transfers after `prepare_transfer` says so.
- **End call**.

## Event subscription
- URL `BASE/webhooks/ringg`, header `Authorization: Bearer <WEBHOOK_SECRET>`
- Event: `all_processing_completed` (the server ignores the rest; unanswered calls are handled by its own re-dial schedule)

## Call settings (all outbound agents)
- Calling hours 10:00–19:00 IST (the server enforces this too), max call length 8 min
- **Retries off**: the server's re-dial schedule decides when to call again (it also sends `retry_count: 0` per call)
- Voicemail detection on, leave voicemail off
- Noise filtering on; interruption sensitivity Default
- Inactivity warning 6 s, end after 20 s

## WhatsApp
Meta WhatsApp Cloud API webhook → `BASE/webhooks/whatsapp` with verify token `WA_VERIFY_TOKEN`; subscribe to `messages`.
Templates: `whatsapp_templates.md`.

## Lead sources
`POST BASE/leads/intake` with header `X-Intake-Secret: <INTAKE_SECRET>`: website forms, Zapier/Make relays of
Meta Lead Ads (send the lead object with `field_data`), and portal exports.
