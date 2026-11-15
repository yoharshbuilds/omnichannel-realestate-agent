"""Ringg API tools. On-call tools must answer fast and return one short `result` sentence
that tells the agent what to say or do next, including on failure."""

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import cadence, clock, costsheet, db, managers
from app.channels import email, whatsapp
from app.emailparse import normalize_email
from app.leads import known_summary, notify_manager, whisper
from app.pricing import availability, availability_text, cost_sheet, inr_spoken
from app.projects import brochure_path, brochure_url, details_line, find_project, spoken_summary, visit_slot_problem
from app.routes.common import project_names, reply, require_tool_secret
from app.scheduler import next_call_slot
from app.scoring import rescore

IST = ZoneInfo("Asia/Kolkata")
router = APIRouter(prefix="/tools", dependencies=[Depends(require_tool_secret)])


def no_project() -> dict:
    return reply(f"I couldn't match that project. Ask which one they mean: {project_names()}.")


# ---------- pre-call ----------

class LeadContextIn(BaseModel):
    phone: str


@router.post("/lead-context")
def lead_context(body: LeadContextIn):
    """Pre-call: everything known about the lead + who to transfer to. Referenced in prompts as
    @((lead_context.known)) and as the transfer target @((lead_context.manager_phone))."""
    lead = db.get_lead(body.phone) or {}
    manager = managers.assign(body.phone, lead.get("project_id"), lead.get("language"))
    return {
        "known": known_summary(lead),
        "is_returning": bool(lead.get("last_call_id")),
        "stage": lead.get("stage") or "new",
        "manager_name": manager["name"],
        "manager_phone": manager["phone"],
        "manager_on_duty": "yes" if managers.on_duty(manager, clock.utcnow()) else "no",
    }


# ---------- on-call: information ----------

class ProjectInfoIn(BaseModel):
    project: str | None = None


@router.post("/project-info")
def project_info(body: ProjectInfoIn):
    project = find_project(body.project)
    if not project:
        return no_project()
    return reply(f"{spoken_summary(project)} Connectivity: {', '.join(project['connectivity'])}.",
                 project_id=project["id"])


class AvailabilityIn(BaseModel):
    project: str | None = None
    bhk: str | None = None
    budget: str | None = None


@router.post("/check-availability")
def check_availability(body: AvailabilityIn):
    return reply(availability_text(availability(body.project, body.bhk, body.budget)))


# ---------- on-call: sending things ----------

class SendBrochureIn(BaseModel):
    call_id: str
    phone: str
    name: str = ""
    project: str
    channel: Literal["whatsapp", "email", "both"] = "whatsapp"
    email: str | None = None


@router.post("/send-brochure")
async def send_brochure(body: SendBrochureIn):
    project = find_project(body.project)
    if not project:
        return no_project()

    done, problems = [], []
    if body.channel in ("whatsapp", "both"):
        r = await whatsapp.send_brochure(call_id=body.call_id, phone=body.phone, name=body.name, project=project,
                                         url=brochure_url(project))
        if r["ok"]:
            await whatsapp.send_details(call_id=body.call_id, phone=body.phone, name=body.name, project=project,
                                        details=details_line(project))
            done.append("on WhatsApp")
        else:
            problems.append("WhatsApp delivery failed, offer email instead")

    if body.channel in ("email", "both"):
        address = normalize_email(body.email)
        if not address:
            problems.append("the email address was not clear, ask the caller to spell it letter by letter")
        else:
            db.upsert_lead(body.phone, email=address)
            r = await email.send_project_details(call_id=body.call_id, phone=body.phone, to=address, name=body.name,
                                                 project=project, pdf=brochure_path(project))
            if r["ok"]:
                done.append(f"by email to {address}")
            else:
                problems.append("email delivery failed")

    db.upsert_lead(body.phone, name=body.name, project_id=project["id"], last_call_id=body.call_id)
    sent = f"Sent the {project['name']} brochure {' and '.join(done)}." if done else "Nothing was sent."
    return reply(" ".join([sent, *(p[0].upper() + p[1:] + "." for p in problems)]), sent=bool(done))


class CostSheetIn(BaseModel):
    call_id: str
    phone: str
    name: str = ""
    project: str
    bhk: str
    floor: int | None = None


@router.post("/send-cost-sheet")
async def send_cost_sheet(body: CostSheetIn):
    project = find_project(body.project)
    if not project:
        return no_project()
    sheet = cost_sheet(project["id"], body.bhk, body.floor)
    if not sheet:
        configs = ", ".join(project["configurations"])
        return reply(f"{project['name']} doesn't have that configuration. It has {configs}.")

    url, _ = costsheet.build(sheet, project, body.name)
    total = inr_spoken(sheet["total"])
    r = await whatsapp.send_cost_sheet(call_id=body.call_id, phone=body.phone, name=body.name, project=project,
                                       label=sheet["config"], total=total, url=url)
    db.upsert_lead(body.phone, project_id=project["id"], bhk=sheet["config"])
    said = (f"For a {sheet['config']} on floor {sheet['floor']}, the agreement value is "
            f"{inr_spoken(sheet['agreement_value'])} and all inclusive about {total}, including GST, "
            f"stamp duty, registration and deposits.")
    if r["ok"]:
        return reply(said + " The detailed cost sheet is on their WhatsApp.", sent=True)
    return reply(said + " The cost sheet couldn't be sent on WhatsApp; offer email.", sent=False)


# ---------- on-call: visits, callbacks, transfers ----------

class BookVisitIn(BaseModel):
    call_id: str
    phone: str
    name: str = ""
    project: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM, 24-hour
    attendees: str | None = None


@router.post("/book-site-visit")
async def book_site_visit(body: BookVisitIn):
    project = find_project(body.project)
    if not project:
        return no_project()
    try:
        when = datetime.fromisoformat(f"{body.date}T{body.time}").replace(tzinfo=IST)
    except ValueError:
        return reply("I couldn't understand the date or time. Please confirm the exact day and time.")

    now = clock.utcnow().astimezone(IST)
    if (problem := visit_slot_problem(project, when, now)):
        return reply(problem, booked=False)

    previous = db.active_visit(body.phone)
    lead = db.get_lead(body.phone) or {}
    manager = managers.assign(body.phone, project["id"], lead.get("language"))
    visit_id = db.add_visit(body.phone, body.name, project["id"], when.isoformat(), body.call_id, manager["id"])
    spoken = when.strftime("%A, %d %B at %I:%M %p")
    if visit_id is None:
        return reply(f"That visit is already booked for {spoken}.", booked=True)

    if previous:  # a reschedule: retire the old visit and its reminders
        db.set_visit_status(previous["id"], "rescheduled")
        db.cancel_tasks(body.phone, f"visit:{previous['id']}")
    with db.connect() as c:
        c.execute("UPDATE visits SET attendees = ? WHERE id = ?", (body.attendees, visit_id))

    db.upsert_lead(body.phone, name=body.name, project_id=project["id"], site_visit_at=when.isoformat(),
                   last_call_id=body.call_id)
    db.advance_stage(body.phone, "visit_booked")
    db.cancel_tasks(body.phone, "speed_to_lead")
    rescore(body.phone)
    cadence.start_visit_concierge(db.get_visit(visit_id), now.astimezone(timezone.utc))

    await whatsapp.send_visit_confirmation(call_id=body.call_id, phone=body.phone, name=body.name,
                                           project=project, when=spoken)
    await notify_manager(body.phone, "site visit " + ("rescheduled" if previous else "booked"),
                         call_id=body.call_id, notes=f"{project['name']} {spoken}")
    return reply(f"Booked a site visit to {project['name']} on {spoken}. {manager['name']} will meet them. "
                 "A confirmation with the address is on WhatsApp.", booked=True, visit_id=visit_id)


class CallbackIn(BaseModel):
    call_id: str
    phone: str
    date: str
    time: str
    with_person: bool = False
    reason: str = ""


@router.post("/book-callback")
async def book_callback(body: CallbackIn):
    try:
        when = datetime.fromisoformat(f"{body.date}T{body.time}").replace(tzinfo=IST)
    except ValueError:
        return reply("I couldn't understand the date or time. Please confirm when to call back.")
    slot = next_call_slot(when.astimezone(timezone.utc)).astimezone(IST)
    spoken = slot.strftime("%A at %I:%M %p")
    moved = "" if slot == when else f" Calls go out only between 10 am and 7 pm, so it's set for {spoken}."
    db.upsert_lead(body.phone, callback_requested=1)
    if body.with_person:
        await notify_manager(body.phone, f"call back {spoken}", call_id=body.call_id, notes=body.reason)
        return reply(f"A sales manager will call them on {spoken}.{moved}")
    cadence.schedule_callback(body.phone, slot.astimezone(timezone.utc), body.reason or "requested")
    return reply(f"Callback booked for {spoken}.{moved}")


class TransferIn(BaseModel):
    call_id: str
    phone: str
    notes: str = ""


@router.post("/prepare-transfer")
async def prepare_transfer(body: TransferIn):
    """Alert the manager with a whisper summary before the agent transfers. If nobody is on duty,
    tell the agent to book a callback with a person instead of transferring into a void."""
    lead = db.get_lead(body.phone) or {}
    manager = managers.assign(body.phone, lead.get("project_id"), lead.get("language"))
    now = clock.utcnow()
    if not managers.on_duty(manager, now):
        return reply("No sales manager is available right now. Do not transfer. Offer a callback from a person "
                     "and book it with book_callback, with_person true.", transfer=False)
    await whatsapp.send_manager_alert(manager=manager, lead_phone=body.phone,
                                      summary="Incoming transfer: " + whisper(lead, body.notes), call_id=body.call_id)
    db.upsert_lead(body.phone, lead_temperature="hot")
    return reply(f"{manager['name']} has the details. Tell the caller you are connecting them to "
                 f"{manager['name']} now, then transfer.", transfer=True, manager_phone=manager["phone"])
