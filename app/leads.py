"""Shared lead operations used by tools, webhooks and the cadence engine."""

from datetime import datetime

from app import db, managers
from app.channels import email, whatsapp
from app.projects import find_project
from app.scoring import rescore


def whisper(lead: dict, notes: str = "") -> str:
    """One breath of context for a sales manager before they pick up."""
    project = find_project(lead.get("project_id"))
    bits = [
        lead.get("name") or "Unknown name",
        lead.get("bhk"),
        lead.get("budget") and f"budget {lead['budget']}",
        project and project["name"],
        lead.get("timeline") and f"buying {lead['timeline']}",
        lead.get("purpose"),
        lead.get("objections") and f"concerns: {lead['objections']}",
        lead.get("site_visit_at") and "visit booked",
        lead.get("score") is not None and f"score {lead['score']}",
        notes,
    ]
    return ", ".join(str(b) for b in bits if b)


def known_summary(lead: dict) -> str:
    """What the agent already knows, phrased for the prompt."""
    if not lead:
        return "New lead, nothing known yet."
    project = find_project(lead.get("project_id"))
    facts = [
        project and f"enquired about {project['name']}",
        lead.get("bhk") and f"wants {lead['bhk']}",
        lead.get("budget") and f"budget {lead['budget']}",
        lead.get("preferred_location") and f"prefers {lead['preferred_location']}",
        lead.get("timeline") and f"timeline {lead['timeline']}",
        lead.get("objections") and f"earlier concerns: {lead['objections']}",
        lead.get("summary") and f"last call: {lead['summary']}",
    ]
    facts = [f for f in facts if f]
    return "; ".join(facts) if facts else "Enquired, but nothing else known yet."


async def notify_manager(phone: str, reason: str, *, call_id: str | None = None, notes: str = "") -> dict:
    lead = db.get_lead(phone) or {"phone": phone}
    manager = managers.assign(phone, lead.get("project_id"), lead.get("language"))
    summary = whisper(lead, notes)
    await whatsapp.send_manager_alert(manager=manager, lead_phone=phone, summary=f"{reason}: {summary}", call_id=call_id)
    await email.notify_sales(call_id=call_id, phone=phone, to=manager["email"],
                             subject=f"[{reason.upper()}] {lead.get('name') or phone}",
                             lines={"Lead": phone, "Summary": summary, "Reason": reason})
    return manager


def mark_engaged(phone: str, now: datetime) -> None:
    db.set_if_empty(phone, "engaged_at", db.iso(now))
    rescore(phone)
