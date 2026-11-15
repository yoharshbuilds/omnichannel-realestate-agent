"""Lead score, 0-100, recomputed after every touch. Rules, not a model: a sales head can read
and argue with them, and there is no outcome data yet to train on. Once visits and bookings
accumulate, refit the weights against them."""

import re

from app import db
from app.pricing import cost_sheet, inventory, match_config, parse_budget


def _timeline_points(t: str) -> int:
    t = t.lower()
    if any(w in t for w in ("immediate", "this month", "asap", "ready")):
        return 25
    months = re.search(r"(\d+)\s*month", t)
    if months:
        m = int(months.group(1))
        return 25 if m <= 3 else 15 if m <= 6 else 5
    if "year" in t:
        return 5
    return 0


def _budget_fit(lead: dict) -> int:
    limit = parse_budget(lead.get("budget"))
    if limit is None:
        return 5
    project_ids = [lead["project_id"]] if lead.get("project_id") else list(inventory())
    for pid in project_ids:
        for u in match_config(inventory().get(pid, {}).get("unit_types", []), lead.get("bhk")):
            if cost_sheet(pid, u["config"])["agreement_value"] <= limit * 1.05:
                return 25
    return 0


def score(lead: dict) -> tuple[int, str, list[str]]:
    """Returns (score, temperature, reasons)."""
    pts, why = 0, []

    def add(n: int, reason: str):
        nonlocal pts
        if n:
            pts += n
            why.append(f"+{n} {reason}")

    add(_budget_fit(lead), "budget fits inventory")
    add(_timeline_points(lead.get("timeline") or ""), "timeline")
    if (lead.get("decision_maker") or "").lower() in ("yes", "true", "self", "both"):
        add(10, "decision-maker engaged")
    if (lead.get("loan_status") or "").lower() in ("pre-approved", "preapproved", "self-funded", "not needed"):
        add(10, "funding ready")
    if lead.get("bhk"):
        add(5, "clear requirement")
    if lead.get("site_visit_at") or lead.get("stage") in ("visit_booked", "visit_done", "negotiation"):
        add(20, "site visit")
    if lead.get("engaged_at"):
        add(5, "replied on WhatsApp")
    if (lead.get("source") or "") in ("referral", "walk_in"):
        add(10, "high-intent source")

    pts = min(pts, 100)
    temperature = "hot" if pts >= 65 else "warm" if pts >= 35 else "cold"
    return pts, temperature, why


def rescore(phone: str, llm_temperature: str | None = None) -> tuple[int, str]:
    """Recompute and store. The rules decide, except that a caller the agent judged 'cold'
    (not interested) is never promoted by the rules."""
    lead = db.get_lead(phone)
    if not lead:
        return 0, "cold"
    pts, temperature, _ = score(lead)
    if (llm_temperature or "").lower() == "cold":
        temperature = "cold"
    db.upsert_lead(phone, score=pts, lead_temperature=temperature)
    return pts, temperature
