"""Live inventory and price arithmetic. The LLM never does money maths: it asks these
functions and reads out the answer, so a quoted figure always matches the cost sheet PDF."""

import json
import re
from functools import lru_cache

from app.config import ROOT
from app.projects import all_projects, find_project


@lru_cache
def inventory() -> dict:
    return {k: v for k, v in json.loads((ROOT / "data" / "inventory.json").read_text()).items() if not k.startswith("_")}


# ---------- money formatting ----------

def inr(n: float) -> str:
    """Indian digit grouping: 11234567 → 'Rs. 1,12,34,567' (PDF fonts have no ₹ glyph)."""
    s = str(int(round(n)))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "Rs. " + ",".join([g for g in [head, *groups] if g] + [tail]) if head or groups else "Rs. " + tail


def inr_spoken(n: float) -> str:
    """How a buyer says it: '1.12 crore', '95.2 lakh'."""
    if n >= 1e7:
        return f"{n / 1e7:.2f}".rstrip("0").rstrip(".") + " crore"
    return f"{n / 1e5:.1f}".rstrip("0").rstrip(".") + " lakh"


def parse_budget(text: str | None) -> int | None:
    """'1.2 crore' / '80 lakh' / '70-80 L' / '1 cr' / '95,00,000' → upper bound in rupees."""
    if not text:
        return None
    t = text.lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return None
    top = max(nums)
    if re.search(r"\b(cr|crore|crores)\b", t):
        return int(top * 1e7)
    if re.search(r"\b(l|lac|lakh|lakhs|lacs)\b", t) or re.search(r"\d\s*l\b", t):
        return int(top * 1e5)
    return int(top) if top > 100000 else None


def match_config(unit_types: list[dict], bhk: str | None) -> list[dict]:
    if not bhk:
        return unit_types
    m = re.search(r"\d", bhk)
    if not m:
        return unit_types
    wanted = m.group()
    return [u for u in unit_types if u["config"].startswith(wanted)]


# ---------- cost sheet ----------

def cost_sheet(project_id: str, config: str, floor: int | None = None) -> dict | None:
    inv = inventory().get(project_id)
    if not inv:
        return None
    unit = next((u for u in match_config(inv["unit_types"], config)), None)
    if not unit:
        return None
    lo, hi = unit["floors"]
    floor = lo if floor is None else max(lo, min(hi, floor))
    c = inv["charges"]
    carpet = unit["carpet_sqft"]

    base = carpet * unit["base_rate_psf"]
    rise_floors = max(0, floor - c["floor_rise_from"] + 1)
    floor_rise = rise_floors * c["floor_rise_psf"] * carpet
    agreement = base + floor_rise + c["parking"]
    gst = agreement * c["gst_pct"] / 100
    stamp = agreement * c["stamp_duty_pct"] / 100
    registration = min(agreement * c["registration_pct"] / 100, c["registration_cap"])
    lines = [
        (f"Base price ({carpet} sq ft carpet x {inr(unit['base_rate_psf'])})", base),
        (f"Floor rise (floor {floor})", floor_rise),
        ("Covered parking", c["parking"]),
        ("Agreement value", agreement),
        (f"GST {c['gst_pct']}%", gst),
        (f"Stamp duty {c['stamp_duty_pct']}%", stamp),
        ("Registration", registration),
        ("Maintenance deposit", c["maintenance_deposit"]),
        ("Legal and documentation", c["legal_charges"]),
    ]
    total = agreement + gst + stamp + registration + c["maintenance_deposit"] + c["legal_charges"]
    return {
        "project_id": project_id, "config": unit["config"], "floor": floor, "carpet_sqft": carpet,
        "lines": lines, "agreement_value": agreement, "total": total,
        "available": unit["available"], "facing": unit["facing"],
    }


# ---------- availability & recommendations ----------

def availability(project_ref: str | None, bhk: str | None, budget: str | None) -> dict:
    """Unit types that match, whether they fit the budget, and in-budget alternatives elsewhere."""
    project = find_project(project_ref)
    limit = parse_budget(budget)
    options = []
    for p in ([project] if project else all_projects()):
        for u in match_config(inventory().get(p["id"], {}).get("unit_types", []), bhk):
            if u["available"] <= 0:
                continue
            sheet = cost_sheet(p["id"], u["config"])
            options.append({"project": p, "config": u["config"], "available": u["available"],
                            "agreement_value": sheet["agreement_value"], "total": sheet["total"],
                            "fits": limit is None or sheet["agreement_value"] <= limit * 1.05})

    alternatives = []
    if project and limit and not any(o["fits"] for o in options):
        for p in all_projects():
            if p["id"] == project["id"]:
                continue
            for u in inventory().get(p["id"], {}).get("unit_types", []):
                sheet = cost_sheet(p["id"], u["config"])
                if sheet["agreement_value"] <= limit * 1.05 and u["available"] > 0:
                    alternatives.append({"project": p, "config": u["config"], "agreement_value": sheet["agreement_value"]})
    return {"options": options, "alternatives": alternatives, "budget": limit}


def availability_text(result: dict) -> str:
    opts = result["options"]
    if not opts:
        return "Nothing matches that configuration right now. Ask if another configuration works."
    parts = []
    for o in opts[:3]:
        few = " Only a few left." if o["available"] <= 5 else ""
        parts.append(f"{o['project']['name']} {o['config']} starts at {inr_spoken(o['agreement_value'])} "
                     f"agreement value, about {inr_spoken(o['total'])} all inclusive.{few}")
    text = " ".join(parts)
    if result["budget"] and not any(o["fits"] for o in opts):
        text += f" That is above their budget of {inr_spoken(result['budget'])}."
        if result["alternatives"]:
            a = max(result["alternatives"], key=lambda x: x["agreement_value"])  # the most home the budget buys
            text += (f" Within budget: {a['project']['name']} {a['config']} in {a['project']['location']} "
                     f"from {inr_spoken(a['agreement_value'])}.")
    return text
