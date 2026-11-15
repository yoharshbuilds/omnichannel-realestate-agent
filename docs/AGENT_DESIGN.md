# Designing the best real-estate sales agent (India, developer presales)

## 1. Start from the business, not the bot

A developer's presales team is judged on one number: **site visits that happen**. Most bookings close
after a visit, so every call exists to move a lead one step closer to standing on the site with a decision-maker.

```
Lead in ─▶ First contact ─▶ Qualified ─▶ Visit booked ─▶ Visit done ─▶ Revisit / negotiation ─▶ Booking ─▶ Post-booking
 (portal,    (speed to       (budget,     (slot, who's     (show-up      (sales manager,        (token,     (payments, loan,
  Meta ad,    lead)           config,      coming)          rate)         offers)                agreement)  possession,
  hoarding,                   timeline)                                                                      referrals)
  CP, walk-in)
```

Where leads leak today (vendor-reported numbers, so treat them as indicative only):
- **Slow first response.** Portal leads often wait hours. The same lead has enquired with 3–5 other projects, and whoever calls first sets the frame.
- **Low first-touch coverage.** Sales teams call the leads that look good and drop the rest.
- **Site-visit no-shows.** A booked visit without reminders, a location pin and a reconfirmation often doesn't happen.
- **No post-visit follow-up.** Objections raised on site never reach anyone who can answer them.
- **Dead database.** Thousands of 6–24-month-old leads that nobody calls, some of whom are now ready.
- **Off-hours enquiries.** Calls to hoarding and newspaper numbers at 9 pm and on Sundays go unanswered. Anarock's ElevenLabs case study says about 30% of conversations happen outside business hours.

**Design principle: the "agent" is a team of agents sharing one lead memory, run by a cadence
engine, each with one job and one success metric.**

## 2. The agent team

| # | Agent | Trigger | Channel | One job | Metric |
|---|---|---|---|---|---|
| A | **Speed-to-lead caller** *(built: v1)* | New lead webhook (portal / Meta Lead Ads / website) | Voice, then WhatsApp | Call within 60 s, qualify, send brochure, book visit | Median time to first call, visit-booked % |
| B | **Inbound hotline** | Call to hoarding / ad / site number, 24×7 | Voice | Answer every call, identify source, qualify, book visit or transfer | Answer rate, after-hours visits booked |
| C | **Visit concierge** | Visit booked | WhatsApp + voice | Reconfirm T-24h, send location pin T-2h, handle reschedules, ask who's coming, offer pickup | **Show-up rate** |
| D | **Post-visit feedback** | Visit marked done | Voice | Within 3 h: what they liked, objections, next step; route to manager | Revisit %, objection capture |
| E | **Nurture** | Long timeline / "just exploring" | WhatsApp first, voice on intent | Monthly value touches (construction update, new tower, approved offer); call only when they click or reply | Re-engagement %, opt-out % |
| F | **Reactivation** | Batch of dormant leads | Voice | "Are you still looking?" → requalify or close the record | Revived leads per 1,000 |
| G | **WhatsApp assistant** | Lead replies on WhatsApp | WhatsApp | Answer questions, send floor plans and videos, book or change visits, hand over to a person | Resolution %, visits booked |
| H | **Post-booking** *(phase 3)* | Booking done | Voice + WhatsApp | Demand-letter and payment reminders, document collection, loan help, possession scheduling | On-time payment % |

The first pilot to sell a developer is **A + C**: fastest to show value, and both are easy to measure.
**F** is the best quick win: it runs on data the client already has and shows results in a week.

## 3. The lead brain (what all agents share)

A single lead record, keyed by phone number and de-duplicated across portals:
- **Profile:** name, language, city, current home (renting/owned), family, NRI or not.
- **Requirement:** budget band, configuration, preferred locations, must-haves (Vastu, east-facing, floor, parking), timeline, purpose (self-use/investment), loan needed and pre-approved?
- **Journey:** source and campaign, every call/WhatsApp/email with summary, assets sent and whether they were opened, visits, objections raised, assigned sales manager, channel partner (CP) tag.
- **Score** (recomputed after every touch), a BANT version for real estate:
  - Budget fits inventory? · Decision-maker on the call / spouse involved? · Need clear? · Timeline ≤ 3 months?
  - Plus: loan pre-approved, visited competitors, engagement (brochure opened, replied).
  - → **Hot** (warm transfer now or within 15 min) / **Warm** (visit push + concierge) / **Nurture** / **Closed-lost (reason)**.

Every agent starts its call with a **pre-call tool** that loads this record, so the agent never asks something it already knows:
*"Last time you mentioned a 3 BHK near Baner under 1.5 crore; is that still the plan?"*

## 4. The conversation: what makes a call feel like a top presales executive

**Structure** (Ringg multi-prompt agent, one node per stage, so each node has a tight prompt):
1. **Open (≤ 10 s).** Name, company, the exact enquiry ("you checked Skyline Heights on 99acres an hour ago"), permission ("2 minutes?"). If busy, book a callback slot right away.
2. **Discover.** One question at a time, conversational, not a form. Budget is asked last and as a range, after rapport.
3. **Recommend.** A **specific unit type** from live inventory, with one reason tied to what they said:
   *"Since your office is in Hinjewadi, Metro One is 12 minutes away, and 2 BHKs there start at 72 lakh."*
4. **Handle objections.** A playbook (below), never pushy.
5. **Close on a visit.** Two concrete slots, not an open question: *"Saturday 11 am or Sunday 4 pm?"* Ask who will come and get both decision-makers there.
6. **Confirm and send.** Read back the slot, send the brochure, floor plan for *their* configuration, location pin and cost sheet on WhatsApp.

**Objection playbook** (each answer comes from approved content, not LLM improvisation):
| Objection | Move |
|---|---|
| "Too expensive" | Show the all-inclusive cost honestly; offer a smaller configuration, another project, or the approved payment plan. Never invent a discount; say "the sales manager can discuss pricing at the visit". |
| "Location is far" | Connectivity facts (metro, IT park, schools, drive times) from the knowledge base. |
| "Just exploring" | Lower the commitment: send a walkthrough video, move to nurture, ask permission to follow up in a month. |
| "Need to discuss with family" | Offer a weekend family visit; send a shareable WhatsApp summary for the spouse or parents. |
| "Under construction is risky" | RERA number, construction progress photos, past delivered projects. |
| "Already seeing project X" | Two honest differentiators. Never criticise a competitor. |
| "I'm a broker" | Route to the channel-partner flow, which tags ownership. |
| "Don't call me" | Apologise, opt out on **all** channels, end. |

**Indian real-estate details the agent must get right:**
- Speak numbers the way buyers do: "ek crore bees lakh", "1.2 crore", lakh/crore, never "12 million".
- **Carpet area** (the RERA figure) vs super built-up; quote carpet unless asked.
- All-inclusive cost: base + floor rise + parking + GST (under construction) + stamp duty and registration (by state) + maintenance deposit. The cost sheet is *generated by the server*, never calculated by the LLM.
- Language by city: Pune = Marathi/Hindi/English, Bengaluru = Kannada/English, Chennai = Tamil, Hyderabad = Telugu. Detect the caller's language and switch to it.
- NRI leads: call in their time zone, WhatsApp-first, video walkthrough, PoA questions go to a person.

**Voice craft:**
- Short turns (≤ 2 sentences), natural backchannels ("achha", "got it"), let the caller interrupt.
- Prices and dates read slowly, and confirmed back.
- One filler line while a tool runs; a pre-call tool for anything needed in the first sentence.
- A female or male voice by A/B test per city; same voice for every touch of one lead.

## 5. Guardrails (what makes it safe to give the agent a developer's brand)
- **Facts only from tools:** inventory, price list, approved offers table, KB (RERA, amenities, connectivity). If it isn't there: "I'll have the sales manager confirm that."
- **No commitments:** no discounts, no possession guarantees, no legal or tax advice, no loan approval promises.
- **No sensitive data collection** on calls: no PAN, Aadhaar or bank details.
- **Compliance** (verify with the client's counsel): DND/NCPR scrub, TRAI-registered number series for sales calls, consent from the enquiry form, calling hours, recording disclosure, opt-out honoured on every channel, DPDP retention and deletion, RERA number in any marketing message.
- **Channel-partner ownership:** if a CP registered the lead first, the agent doesn't create a duplicate "direct" lead.

## 6. Smart follow-up content (the wow factor)
After the call, WhatsApp gets a **personalised pack**, not a generic brochure:
1. Brochure (PDF, document template).
2. Floor plan for the configuration discussed.
3. **Personalised cost sheet PDF** for that configuration and floor, generated from the price list.
4. Location pin + "drive time from your office" (if they mentioned it).
5. 60-second walkthrough video.
6. One-line summary they can forward to family.

Opens and clicks feed back into the lead score; a click 3 days later triggers a callback.

## 7. Cadence engine (when to touch, on which channel)
Speed-to-lead, no answer:
```
T+0      call (within 60 s of the lead)
T+1 min  WhatsApp: intro + brochure (template)       ← most first replies come here
T+30 min call
T+4 h    call
Day 2    call in the evening window (18:00–19:30)
Day 3    WhatsApp: "Should I stop calling?" quick replies (Yes / Call me later / Send details)
Day 7+   → Nurture (E)
```
Limits: max 3 calls/day and 6/week per lead, calling hours enforced, stop on opt-out, stop when a person owns the lead.

Visit concierge: T-24h reconfirm (WhatsApp quick reply, call if no reply) → T-2h location pin + manager's name → T+15 min after slot: "running late?" call → no-show: reschedule call same evening.

## 8. Human handoff
- **Warm transfer** during working hours for hot leads, using the sales manager's **round-robin** by language and project. A **whisper summary** is read to the manager before the caller joins: *"Harsh, 3 BHK, 1.4 crore, Baner, buying in 2 months, wife joining the visit."*
- Off-hours: book a slot on the manager's calendar and push the summary to the CRM and WhatsApp.
- The manager's outcome (visit done, booked, lost + reason) flows back to train the score.

## 9. Integrations an FDE would build
| System | Why |
|---|---|
| Lead sources: 99acres, MagicBricks, Housing.com, Meta Lead Ads, Google Ads, website | Speed-to-lead trigger, source attribution |
| CRM: Sell.Do, LeadSquared, Salesforce, Zoho | Lead record, ownership, dispositions |
| Inventory / price list (often a Google Sheet or the ERP) | Live availability, cost sheet |
| Sales managers' calendars | Visit slots, warm-transfer availability |
| WhatsApp BSP (Meta Cloud, Gupshup, Interakt) | Templates, documents, quick replies |
| Ringg | Voice agents, campaigns, analysis, webhooks |

## 10. Measuring it
Funnel per source and per agent version: **speed-to-lead (median seconds) → connect % → qualified % → visit booked % → show-up % → booking %**,
plus cost per visit done, opt-out %, warm-transfer acceptance, policy violations from evals.
The client's question is *"how many more visits, at what cost per visit?"*. The dashboard answers that first.

**Evals before launch:** simulated callers, each scored on a rubric (facts correct, no invented offers, one question per turn, visit asked for, opt-out honoured, language matched):
first-time buyer couple · NRI investor at 2 am IST · price haggler · broker pretending to be a buyer ·
Marathi-only senior citizen · "stop calling me" · caller in traffic noise · confused caller who enquired for a different project.

## 11. What's built in this repo
| Piece | Status |
|---|---|
| Speed-to-lead call, brochure on WhatsApp/email, visit booking, post-call sync | ✅ |
| Lead intake (website, Meta Lead Ads, portals) + re-dial cadence with calling window and limits | ✅ |
| Visit concierge: reminders with buttons, reconfirm call, location pin, late check, no-show recovery | ✅ |
| Live inventory, budget fit, in-budget alternatives, personalised cost sheet PDF | ✅ |
| Lead score, manager assignment, warm transfer with whisper summary | ✅ |
| WhatsApp replies → actions; opt-out on every channel | ✅ |
| Post-visit feedback, reactivation and nurture campaigns | ✅ |
| Persona evals (Claude agent + caller + judge) and funnel dashboard | ✅ |
| Post-booking agent (payments, documents, possession) | not started |
| CRM connectors (Sell.Do / LeadSquared / Salesforce) | not started: the lead record and webhook outcomes are ready to map |
