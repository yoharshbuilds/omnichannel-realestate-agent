"""Post-call processing for Ringg's `all_processing_completed` event.

The flow (custom arg set when we placed the call) says which playbook the call belonged to.
Unanswered calls need nothing here: the cadence engine already has the next touch queued.
Answered calls update the lead, move it through the funnel, stop the re-dial ladder,
schedule what the caller asked for, and send anything promised but not yet sent."""

import logging
from datetime import datetime, timedelta

from app import cadence, clock, db
from app.channels import email, whatsapp
from app.emailparse import normalize_email
from app.leads import notify_manager
from app.projects import brochure_path, brochure_url, find_project
from app.scheduler import next_call_slot
from app.scoring import rescore

log = logging.getLogger("followup")
LONG_TIMELINE = ("year", "12 month", "exploring", "not sure", "just looking")


def truthy(v) -> bool:
    return v is True or str(v).strip().lower() in {"true", "yes", "y", "1"}


def caller_phone(payload: dict) -> str:
    args = payload.get("custom_args_values") or {}
    if args.get("mobile_number"):
        return args["mobile_number"]
    if payload.get("call_type") == "inbound":
        return payload.get("from_number", "")
    return payload.get("to_number", "")


def analysis(payload: dict, key: str) -> dict:
    data = payload.get(key) or {}
    return data.get("analysis_data", data) if isinstance(data, dict) else {}


def connected(payload: dict) -> bool:
    if payload.get("status") != "completed":
        return False
    n = payload.get("user_message_count")
    return n is None or int(n) > 0  # completed with zero caller turns = voicemail / silence


def _callback_time(pa: dict, now: datetime) -> datetime:
    try:
        when = datetime.fromisoformat(str(pa.get("callback_requested_time")))
        when = when if when.tzinfo else when.replace(tzinfo=cadence.IST)
        if when > now:
            return next_call_slot(when)
    except ValueError:
        pass
    return next_call_slot(now + timedelta(hours=2))


async def handle_call_finished(payload: dict, now: datetime | None = None) -> None:
    now = now or clock.utcnow()
    call_id = payload["call_id"]
    phone = caller_phone(payload)
    args = payload.get("custom_args_values") or {}
    flow = args.get("flow") or ("inbound" if payload.get("call_type") == "inbound" else "speed_to_lead")
    name = args.get("callee_name", "")

    if not connected(payload):
        db.upsert_lead(phone, last_call_id=call_id)
        log.info("call %s (%s) not connected: %s", call_id, flow, payload.get("status"))
        return

    a, pa = analysis(payload, "client_analysis"), analysis(payload, "platform_analysis")
    db.set_if_empty(phone, "first_connect_at", db.iso(now))

    if truthy(a.get("opt_out")):
        cadence.opt_out(phone)
        return

    project = find_project(a.get("interested_project")) or find_project(args.get("project_id") or args.get("project"))
    lead_email = normalize_email(a.get("email"))
    db.upsert_lead(
        phone,
        name=a.get("customer_name") or name,
        email=lead_email,
        language=a.get("language"),
        project_id=project and project["id"],
        budget=a.get("budget"),
        bhk=a.get("bhk_preference"),
        preferred_location=a.get("preferred_location"),
        timeline=a.get("purchase_timeline"),
        purpose=a.get("purpose"),
        loan_status=a.get("loan_status"),
        decision_maker=a.get("decision_maker_present"),
        objections=a.get("objections"),
        last_call_id=call_id,
        last_contact_at=db.iso(now),
        summary=pa.get("summary"),
    )
    db.advance_stage(phone, "contacted")
    db.cancel_tasks(phone, "speed_to_lead")  # reached: stop the re-dial ladder
    db.cancel_tasks(phone, "reactivation")

    # ---- flow-specific outcomes ----
    visit_id = int(args["visit_id"]) if str(args.get("visit_id", "")).isdigit() else None
    if flow in ("visit_reconfirm", "visit_late_check") and visit_id and truthy(a.get("visit_confirmed")):
        db.set_visit_status(visit_id, "confirmed")
    next_step = str(a.get("next_step", "")).lower()
    if flow == "post_visit_feedback":
        if next_step in ("booking", "negotiation"):
            db.advance_stage(phone, "negotiation")
            await notify_manager(phone, "ready to negotiate", call_id=call_id, notes=a.get("visit_feedback", ""))
        elif next_step == "not_interested":
            db.upsert_lead(phone, stage="lost", lost_reason=a.get("objections") or "not interested after visit")

    # ---- funnel position ----
    lead = db.get_lead(phone)
    if next_step == "not_interested" or truthy(a.get("not_interested")):
        if lead["stage"] not in ("negotiation", "booked"):
            db.upsert_lead(phone, stage="lost", lost_reason=a.get("objections") or "not interested")
    elif lead.get("bhk") or lead.get("budget"):
        db.advance_stage(phone, "qualified")
        timeline = (lead.get("timeline") or "").lower()
        if lead["stage"] in ("contacted", "qualified") and any(w in timeline for w in LONG_TIMELINE):
            db.upsert_lead(phone, stage="nurture")

    if truthy(a.get("callback_requested")) or truthy(pa.get("callback_requested")):
        db.upsert_lead(phone, callback_requested=1)
        cadence.schedule_callback(phone, _callback_time(pa, now))

    # ---- anything promised but not sent during the call ----
    if project:
        if truthy(a.get("wants_brochure")) and not db.was_sent(call_id, "whatsapp", "brochure"):
            await whatsapp.send_brochure(call_id=call_id, phone=phone, name=name, project=project,
                                         url=brochure_url(project))
        if lead_email and not db.was_sent(call_id, "email", "details"):
            await email.send_project_details(call_id=call_id, phone=phone, to=lead_email, name=name,
                                             project=project, pdf=brochure_path(project))

    _, temperature = rescore(phone, a.get("lead_temperature"))
    lead = db.get_lead(phone)
    if temperature == "hot" and lead["stage"] not in ("visit_booked", "negotiation", "booked") \
            and flow != "post_visit_feedback":
        await notify_manager(phone, "hot lead", call_id=call_id)
