"""Lead intake, visit outcomes and campaigns. These are called by the client's systems
(website, Meta Lead Ads relay, CRM) or by the sales team, not by the voice agent."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app import cadence, clock, db, intake, scheduler
from app.leads import notify_manager
from app.routes.common import require_intake_secret

router = APIRouter(dependencies=[Depends(require_intake_secret)])
ACTIVE_STAGES = ("visit_booked", "visit_done", "negotiation", "booked")


@router.post("/leads/intake")
async def lead_intake(body: dict, tasks: BackgroundTasks):
    """New enquiry → speed-to-lead playbook. Accepts website JSON, Meta Lead Ads lead objects
    and portal exports (see app/intake.py)."""
    lead = intake.normalize(body)
    if not lead["phone"]:
        raise HTTPException(422, "no valid phone number")
    phone, now = lead["phone"], clock.utcnow()
    existing = db.get_lead(phone)

    db.upsert_lead(phone, name=lead["name"], email=lead["email"], project_id=lead["project_id"],
                   campaign=lead["campaign"],
                   # first touch wins attribution, and a channel partner keeps ownership
                   source=None if existing else lead["source"],
                   cp_id=None if existing and existing.get("cp_id") else lead["cp_id"])

    if existing and existing.get("opted_out"):
        return {"ok": True, "phone": phone, "action": "ignored_opted_out"}
    if existing and existing.get("stage") in ACTIVE_STAGES:
        await notify_manager(phone, "re-enquired", notes=f"via {lead['source']}")
        return {"ok": True, "phone": phone, "action": "notified_owner"}

    db.upsert_lead(phone, stage="new")
    cadence.start_speed_to_lead(phone, now)
    tasks.add_task(scheduler.run_due)  # don't wait for the next tick: the first minute matters
    return {"ok": True, "phone": phone, "action": "speed_to_lead_started"}


class ImportIn(BaseModel):
    leads: list[dict]
    stage: str = "nurture"


@router.post("/leads/import")
def lead_import(body: ImportIn):
    """Load an old database (no calls are placed; use /campaigns/reactivation for that)."""
    added, skipped = 0, 0
    for raw in body.leads:
        lead = intake.normalize(raw)
        if not lead["phone"]:
            skipped += 1
            continue
        db.upsert_lead(lead["phone"], name=lead["name"], email=lead["email"], project_id=lead["project_id"],
                       source=lead["source"], campaign=lead["campaign"], cp_id=lead["cp_id"],
                       last_contact_at=raw.get("last_contact_at"))
        if (db.get_lead(lead["phone"]) or {}).get("stage") not in ACTIVE_STAGES:
            db.upsert_lead(lead["phone"], stage=body.stage)
        added += 1
    return {"ok": True, "imported": added, "skipped": skipped}


class OutcomeIn(BaseModel):
    status: Literal["done", "no_show", "cancelled"]


@router.post("/visits/{visit_id}/outcome")
def visit_outcome(visit_id: int, body: OutcomeIn):
    try:
        return cadence.visit_outcome(visit_id, body.status, clock.utcnow())
    except KeyError:
        raise HTTPException(404, "visit not found")


class ReactivationIn(BaseModel):
    older_than_days: int = 90
    limit: int = 100
    project: str | None = None
    spacing_seconds: int = 30


@router.post("/campaigns/reactivation")
def reactivation(body: ReactivationIn):
    now = clock.utcnow()
    leads = db.dormant_leads(db.iso(now - timedelta(days=body.older_than_days)), body.limit, body.project)
    scheduled = 0
    for i, lead in enumerate(leads):
        if cadence.schedule(lead["phone"], "call", "reactivation", now + timedelta(seconds=i * body.spacing_seconds),
                            {"flow": "reactivation"}, dedupe=f"react:{lead['phone']}:{now.date()}"):
            scheduled += 1
    return {"ok": True, "scheduled": scheduled}


class NurtureIn(BaseModel):
    update: str  # one line, e.g. "Tower C launched with 2 BHKs from 92 lakh; site visits open this weekend"
    stage: str = "nurture"


@router.post("/campaigns/nurture")
def nurture(body: NurtureIn):
    now = clock.utcnow()
    leads = db.leads_in_stage(body.stage)
    for lead in leads:
        cadence.schedule(lead["phone"], "whatsapp", "nurture", now,
                         {"template": "nurture", "update": body.update[:500]},
                         dedupe=f"nurture:{lead['phone']}:{now.date()}")
    return {"ok": True, "scheduled": len(leads)}
