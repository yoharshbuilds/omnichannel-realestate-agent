# WhatsApp templates to submit for approval (Meta WhatsApp Manager)

Category **Utility** unless noted, language English (`en`). Variable order must match `app/channels/whatsapp.py`.
Body variables can't contain newlines or tabs. Quick-reply buttons must be in the order listed, because the server
sends a payload per button index.

| Name | Header | Body | Buttons |
|---|---|---|---|
| `brochure_share` | Document | Hi {{1}}, thank you for your interest in {{2}}. Here is the brochure. Reply here if you'd like to book a site visit. | — |
| `project_details` | — | Hi {{1}}, here are the key details of {{2}}: {{3}}. Our advisor will be happy to answer any questions. | — |
| `cost_sheet` | Document | Hi {{1}}, here is the cost sheet for {{2}}. All-inclusive estimate: {{3}}. Final figures are confirmed in the allotment letter. | — |
| `site_visit_confirmation` | — | Hi {{1}}, your site visit to {{2}} is confirmed for {{3}}. Address: {{4}}. | — |
| `visit_reminder` | — | Hi {{1}}, a reminder of your visit to {{2}} on {{3}}. Will you be coming? | Quick reply: "Yes, I'll be there" · "Reschedule" |
| `visit_location` | Location | Hi {{1}}, see you {{2}}. Here's the location. {{3}} ({{4}}) will meet you at the sales lounge. | — |
| `stop_check` (Marketing) | — | Hi {{1}}, we tried reaching you about {{2}}. What would you prefer? | Quick reply: "Send details" · "Call me later" · "Stop" |
| `nurture_update` (Marketing) | — | Hi {{1}}, an update from Skyline Homes: {{2}} | Quick reply: "Call me" · "Stop" |
| `manager_lead_alert` | — | {{1}}, lead {{2}}: {{3}} | — |

Inbound replies are handled in `app/routes/webhooks.py`:
- button payloads `VISIT_CONFIRM:<id>`, `VISIT_RESCHEDULE:<id>`, `SEND_DETAILS`, `CALL_LATER`, `CALL_ME`, `STOP`
- "stop" / "unsubscribe" typed as text → opt-out on every channel
- any other text → the owning manager is alerted, and a dormant lead gets a call in 10 minutes

## Using Ringg's own WhatsApp tools instead
Ringg's built-in **Send WhatsApp Template / Text** tools (on-call and post-call) need a WhatsApp Business account
connected in Ringg. Their docs don't mention document or location headers, so PDFs and location pins go through
this service. Plain-text templates could move to Ringg's tool if preferred.
