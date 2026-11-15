"""Inbound webhooks: Ringg call events and WhatsApp messages from leads."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app import cadence, clock, db
from app.channels import whatsapp
from app.config import get_settings
from app.followup import handle_call_finished
from app.leads import mark_engaged, notify_manager
from app.projects import brochure_url, details_line, find_project
from app.routes.common import secret_ok
from app.scheduler import next_call_slot

log = logging.getLogger("webhooks")
router = APIRouter(prefix="/webhooks")
STOP_WORDS = {"stop", "unsubscribe", "stop calling", "don't call", "dont call", "not interested"}


@router.post("/ringg")
async def ringg_webhook(request: Request, tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    if not secret_ok(authorization, get_settings().webhook_secret):
        raise HTTPException(401, "bad webhook secret")
    payload = await request.json()
    event, call_id = payload.get("event_type"), payload.get("call_id")
    if not event or not call_id:
        raise HTTPException(400, "missing event_type or call_id")
    # Store every event first; duplicates are acknowledged but not processed twice.
    if not db.record_event(call_id, event, payload):
        return {"ok": True, "duplicate": True}
    if event == "all_processing_completed":
        tasks.add_task(handle_call_finished, payload)
    return {"ok": True}


@router.get("/whatsapp")
def whatsapp_verify(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
                    challenge: str = Query(alias="hub.challenge")):
    """Meta's one-time subscription handshake."""
    if mode == "subscribe" and secret_ok(token, get_settings().wa_verify_token):
        return PlainTextResponse(challenge)
    raise HTTPException(403, "verification failed")


@router.post("/whatsapp")
async def whatsapp_inbound(request: Request):
    # Production: verify X-Hub-Signature-256 with the app secret before trusting the body.
    body = await request.json()
    handled = []
    for msg in whatsapp.parse_inbound(body):
        if not db.record_inbound(msg["phone"], msg["id"], msg["type"], msg["text"], msg["raw"]):
            continue
        handled.append(await handle_reply(msg["phone"], msg["payload"], msg["text"], clock.utcnow()))
    return {"ok": True, "handled": handled}


async def handle_reply(phone: str, payload: str, text: str, now: datetime) -> str:
    """A reply opens WhatsApp's 24-hour window, so we can answer in free text."""
    action, _, ref = payload.partition(":")
    lead = db.get_lead(phone) or {}
    name = (lead.get("name") or "").split(" ")[0] or "there"
    if not lead:
        db.upsert_lead(phone, source="whatsapp_inbound")
    mark_engaged(phone, now)

    if action == "STOP" or text.strip().lower() in STOP_WORDS:
        cadence.opt_out(phone)
        await whatsapp.send_text(phone=phone, text="Done. You won't receive any more calls or messages from us.")
        return "opt_out"

    if action == "VISIT_CONFIRM" and ref.isdigit():
        visit = db.get_visit(int(ref))
        if visit and visit["status"] == "booked":
            db.set_visit_status(visit["id"], "confirmed")
        await whatsapp.send_text(phone=phone, text=f"Thanks {name}, your visit is confirmed. See you there!")
        return "visit_confirmed"

    if action == "VISIT_RESCHEDULE" and ref.isdigit():
        cadence.schedule(phone, "call", f"visit:{ref}", next_call_slot(now),
                         {"flow": "visit_reschedule", "visit_id": int(ref)}, dedupe=f"visit:{ref}:resched:{db.iso(now)}")
        await whatsapp.send_text(phone=phone, text="No problem. We'll call you shortly to pick a new time.")
        return "visit_reschedule"

    if action == "SEND_DETAILS":
        project = find_project(lead.get("project_id"))
        if project:
            await whatsapp.send_brochure(call_id=None, phone=phone, name=name, project=project, url=brochure_url(project))
            await whatsapp.send_text(phone=phone, text=f"{project['name']}: {details_line(project)}.")
        return "details_sent"

    if action == "CALL_LATER":
        cadence.schedule_callback(phone, next_call_slot(cadence.at_ist(now + timedelta(days=1), 11)), "asked later")
        await whatsapp.send_text(phone=phone, text="Sure, we'll call you tomorrow around 11 am.")
        return "call_later"

    if action == "CALL_ME":
        cadence.schedule_callback(phone, next_call_slot(now + timedelta(minutes=2)), "asked for a call")
        await whatsapp.send_text(phone=phone, text="Calling you in a couple of minutes.")
        return "call_me"

    # Free text is a buying signal: a person should see it, and a dormant lead gets a call.
    await notify_manager(phone, "whatsapp reply", notes=text[:300])
    if (lead.get("stage") or "new") in ("nurture", "attempting", "lost", "new"):
        cadence.schedule_callback(phone, next_call_slot(now + timedelta(minutes=10)), "replied on WhatsApp")
    return "free_text"
