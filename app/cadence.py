"""Playbooks: which touches happen when, and the task handlers that perform them.

speed_to_lead    new enquiry → call in under a minute, WhatsApp, re-dial ladder, "should I stop?", nurture
visit:<id>       booked visit → reminder with Confirm/Reschedule, reconfirm call, location pin, late check
feedback:<id>    visit done → feedback call a few hours later
callback         caller asked to be called at a specific time"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db, managers, ringg
from app.channels import whatsapp
from app.intake import spoken_source
from app.projects import brochure_url, find_project
from app.scheduler import Postpone, Skip, call_limit_reason, handler, next_call_slot, schedule

IST = ZoneInfo("Asia/Kolkata")
STL_ACTIVE = ["new", "attempting"]
VISIT_ACTIVE = ["booked", "confirmed"]
WA_HOURS = (9, 21)  # no WhatsApp pings at night


def at_ist(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.astimezone(IST).replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def spoken_time(ts: str) -> str:
    return db.parse(ts).astimezone(IST).strftime("%A, %d %B at %I:%M %p")


# ---------- playbooks ----------

STL_GUARD = {"stage_in": STL_ACTIVE}


def start_speed_to_lead(phone: str, now: datetime) -> None:
    """Only the first dial is scheduled up front. Each later step is scheduled from the moment the
    previous call actually went out (see _after_speed_call), so a lead that arrives at night
    doesn't get its whole re-dial ladder at 10:00 the next morning."""
    ladder = f"stl:{phone}:{now.astimezone(IST).date()}"
    schedule(phone, "call", "speed_to_lead", now,
             {"flow": "speed_to_lead", "attempt": 1, "ladder": ladder, "guard": STL_GUARD}, dedupe=f"{ladder}:call1")


def _after_speed_call(phone: str, attempt: int, ladder: str, placed_at: datetime) -> None:
    def step(kind: str, when: datetime, name: str, **extra):
        schedule(phone, kind, "speed_to_lead", when,
                 {"flow": "speed_to_lead", "ladder": ladder, "guard": STL_GUARD, **extra}, dedupe=f"{ladder}:{name}")

    if attempt == 1:
        step("whatsapp", placed_at + timedelta(minutes=1), "intro", template="intro")
        step("call", placed_at + timedelta(minutes=30), "call2", attempt=2)
        step("whatsapp", at_ist(placed_at + timedelta(days=2), 11), "stop_check", template="stop_check")
        step("set_stage", placed_at + timedelta(days=7), "nurture", stage="nurture")
    elif attempt == 2:
        step("call", placed_at + timedelta(hours=4), "call3", attempt=3)
    elif attempt == 3:
        step("call", at_ist(placed_at + timedelta(days=1), 18, 30), "call4", attempt=4)  # try the evening


def start_visit_concierge(visit: dict, now: datetime) -> None:
    vid, when = visit["id"], db.parse(visit["visit_at"])
    flow, phone = f"visit:{vid}", visit["phone"]
    unconfirmed = {"visit_id": vid, "visit_status_in": ["booked"], "not_after": db.iso(when - timedelta(hours=3))}
    active = {"visit_id": vid, "visit_status_in": VISIT_ACTIVE}

    if when - timedelta(hours=24) > now:
        schedule(phone, "whatsapp", flow, when - timedelta(hours=24),
                 {"template": "visit_reminder", "visit_id": vid, "guard": unconfirmed}, dedupe=f"{flow}:reminder")
        schedule(phone, "call", flow, when - timedelta(hours=20),
                 {"flow": "visit_reconfirm", "visit_id": vid, "guard": unconfirmed}, dedupe=f"{flow}:reconfirm")
    if when - timedelta(hours=2) > now:
        schedule(phone, "whatsapp", flow, when - timedelta(hours=2),
                 {"template": "visit_location", "visit_id": vid,
                  "guard": {**active, "not_after": db.iso(when)}}, dedupe=f"{flow}:location")
    schedule(phone, "call", flow, when + timedelta(minutes=30),
             {"flow": "visit_late_check", "visit_id": vid,
              "guard": {**active, "not_after": db.iso(when + timedelta(hours=3))}}, dedupe=f"{flow}:late")


def visit_outcome(visit_id: int, status: str, now: datetime) -> dict:
    """Sales manager (or the CRM) marks what happened at the visit."""
    visit = db.get_visit(visit_id)
    if not visit:
        raise KeyError(visit_id)
    db.set_visit_status(visit_id, status)
    db.cancel_tasks(visit["phone"], f"visit:{visit_id}")
    phone = visit["phone"]
    if status == "done":
        db.advance_stage(phone, "visit_done")
        schedule(phone, "call", f"feedback:{visit_id}", now + timedelta(hours=3),
                 {"flow": "post_visit_feedback", "visit_id": visit_id}, dedupe=f"feedback:{visit_id}")
    elif status == "no_show":
        db.upsert_lead(phone, stage="qualified")
        schedule(phone, "call", f"visit:{visit_id}", now + timedelta(hours=1),
                 {"flow": "visit_recovery", "visit_id": visit_id}, dedupe=f"visit:{visit_id}:recovery")
    return db.get_visit(visit_id)


def schedule_callback(phone: str, when: datetime, reason: str = "callback") -> None:
    schedule(phone, "call", "callback", when, {"flow": "callback", "reason": reason},
             dedupe=f"cb:{phone}:{db.iso(when)}")


def opt_out(phone: str) -> None:
    db.upsert_lead(phone, opted_out=1, stage="opted_out")
    db.cancel_tasks(phone)


# ---------- handlers ----------

def call_context(lead: dict, payload: dict) -> dict:
    """Custom args for the Ringg agent, so it never asks what we already know."""
    project = find_project(lead.get("project_id"))
    args = {
        "project": project["name"] if project else "",
        "project_id": lead.get("project_id") or "",
        "lead_source": spoken_source(lead.get("source")),
        "known_budget": lead.get("budget") or "",
        "known_bhk": lead.get("bhk") or "",
        "known_timeline": lead.get("timeline") or "",
        "attempt": payload.get("attempt", ""),
        "language": lead.get("language") or "",
    }
    if (vid := payload.get("visit_id")) and (visit := db.get_visit(vid)):
        vp = find_project(visit["project_id"])
        args |= {"visit_id": vid, "visit_time": spoken_time(visit["visit_at"]),
                 "visit_project": vp["name"] if vp else "", "visit_address": vp["site_address"] if vp else ""}
    return args


@handler("call")
async def run_call(task: dict, now: datetime) -> str:
    phone, p = task["phone"], task["payload"]
    slot = next_call_slot(now)
    if slot > now:
        raise Postpone(slot, "outside calling hours")
    if (reason := call_limit_reason(phone, now)):
        raise Postpone(next_call_slot(at_ist(now + timedelta(days=1), 0)), reason)

    lead = db.get_lead(phone) or {}
    r = await ringg.start_call(phone=phone, name=lead.get("name") or "", flow=p["flow"],
                               custom_args=call_context(lead, p))
    if not r["ok"]:
        raise RuntimeError(r["error"])
    db.set_if_empty(phone, "first_call_at", db.iso(now))
    db.upsert_lead(phone, last_contact_at=db.iso(now))
    db.advance_stage(phone, "attempting")
    if p["flow"] == "speed_to_lead" and p.get("ladder"):
        _after_speed_call(phone, int(p["attempt"]), p["ladder"], now)
    return f"call {r['call_id']}"


@handler("whatsapp")
async def run_whatsapp(task: dict, now: datetime) -> str:
    phone, p = task["phone"], task["payload"]
    hour = now.astimezone(IST).hour
    if not WA_HOURS[0] <= hour < WA_HOURS[1]:
        nxt = at_ist(now if hour < WA_HOURS[0] else now + timedelta(days=1), WA_HOURS[0])
        raise Postpone(nxt, "quiet hours")

    lead = db.get_lead(phone) or {}
    name = lead.get("name") or ""
    project = find_project(lead.get("project_id"))
    template = p["template"]

    if template in ("visit_reminder", "visit_location"):
        visit = db.get_visit(p["visit_id"])
        project = find_project(visit["project_id"])
        when = spoken_time(visit["visit_at"])
        if template == "visit_reminder":
            r = await whatsapp.send_visit_reminder(phone=phone, name=name, project=project, when=when,
                                                   visit_id=visit["id"])
        else:
            manager = managers.get(visit["manager_id"]) or managers.assign(phone, project["id"], lead.get("language"))
            r = await whatsapp.send_visit_location(phone=phone, name=name, project=project, when=when, manager=manager)
    elif template == "nurture":
        r = await whatsapp.send_nurture(phone=phone, name=name, update=p["update"])
    elif not project:
        raise Skip("no project to talk about")
    elif template == "intro":
        r = await whatsapp.send_brochure(call_id=None, phone=phone, name=name, project=project,
                                         url=brochure_url(project))
    elif template == "stop_check":
        r = await whatsapp.send_stop_check(phone=phone, name=name, project=project)
    else:
        raise ValueError(f"unknown template {template}")

    if not r["ok"]:
        raise RuntimeError(r["error"])
    db.upsert_lead(phone, last_contact_at=db.iso(now))
    return f"whatsapp {template}"


@handler("set_stage")
async def run_set_stage(task: dict, now: datetime) -> str:
    db.upsert_lead(task["phone"], stage=task["payload"]["stage"])
    return f"stage → {task['payload']['stage']}"
