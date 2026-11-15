"""Project catalogue. In a real deployment this would come from the developer's CRM or inventory system."""

import json
from datetime import datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from app.config import ROOT, get_settings

CATALOG = ROOT / "data" / "projects.json"
BROCHURES = ROOT / "static" / "brochures"


@lru_cache
def all_projects() -> list[dict]:
    return json.loads(CATALOG.read_text())


def find_project(ref: str | None) -> dict | None:
    """Match an id, a name, or whatever the LLM heard ("skyline", "palm grove villas")."""
    if not ref:
        return None
    key = ref.strip().lower()
    for p in all_projects():
        if key == p["id"] or key == p["name"].lower() or key in p["aliases"]:
            return p
    for p in all_projects():
        if any(a in key for a in p["aliases"]):
            return p
    return None


def brochure_path(project: dict) -> Path:
    return BROCHURES / project["brochure_file"]


def brochure_url(project: dict) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/static/brochures/{project['brochure_file']}"


def details_line(project: dict) -> str:
    """One line, safe for a WhatsApp template variable (no newlines or tabs allowed there)."""
    return (
        f"{', '.join(project['configurations'])} in {project['location']}, "
        f"{project['price_range']}, possession {project['possession']}"
    )


def visit_slot_problem(project: dict, when: datetime, now: datetime) -> str | None:
    """Reason a site-visit slot can't be booked, phrased so the agent can say it, or None if it's fine.
    visit_hours looks like "10:00-19:00, all days" or "10:00-18:00, closed Tuesday"."""
    hours, _, days = project["visit_hours"].partition(",")
    start, end = (time.fromisoformat(t.strip()) for t in hours.split("-"))
    if when <= now:
        return "That time has already passed. Please suggest a later date and time."
    if when > now + timedelta(days=60):
        return "Visits can be booked up to 60 days ahead. Please suggest an earlier date."
    if not (start <= when.time() < end):
        return f"Site visits run between {start:%I:%M %p} and {end:%I:%M %p}. Please suggest a time in that window."
    closed = days.strip().lower().removeprefix("closed").strip()
    if days.strip().lower().startswith("closed") and when.strftime("%A").lower() == closed:
        return f"The site is closed on {closed.title()}s. Please suggest another day."
    return None


def spoken_summary(project: dict) -> str:
    """Short text the voice agent can read out."""
    return (
        f"{project['name']} in {project['location']}: {', '.join(project['configurations'])}, "
        f"{project['carpet_area']}, priced {project['price_range']}. {project['status']}, "
        f"possession {project['possession']}. Amenities include {', '.join(project['amenities'][:3])}."
    )
