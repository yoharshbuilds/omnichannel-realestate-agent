"""Sales managers: who owns a lead, and who can take a live transfer right now."""

import json
from datetime import datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

from app import db
from app.config import ROOT

IST = ZoneInfo("Asia/Kolkata")


@lru_cache
def all_managers() -> list[dict]:
    return json.loads((ROOT / "data" / "managers.json").read_text())


def get(manager_id: str | None) -> dict | None:
    return next((m for m in all_managers() if m["id"] == manager_id), None)


def on_duty(manager: dict, at: datetime) -> bool:
    start, end = (time.fromisoformat(t) for t in manager["shift"].split("-"))
    return start <= at.astimezone(IST).time() < end


def assign(phone: str, project_id: str | None, language: str | None) -> dict:
    """Keep an existing owner; otherwise the least-loaded manager who sells the project and speaks
    the language, falling back to project-only, then anyone."""
    lead = db.get_lead(phone) or {}
    if (current := get(lead.get("manager_id"))):
        return current
    lang = (language or "").lower()
    load = db.count_leads_by_manager()
    pools = [
        [m for m in all_managers() if project_id in m["projects"] and lang in m["languages"]],
        [m for m in all_managers() if project_id in m["projects"]],
        all_managers(),
    ]
    pool = next(p for p in pools if p)
    chosen = min(pool, key=lambda m: (load.get(m["id"], 0), m["id"]))
    db.upsert_lead(phone, manager_id=chosen["id"])
    return chosen
