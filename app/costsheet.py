"""Personalised cost sheet PDF. Files are served from an unguessable path because they carry
the lead's name; WhatsApp fetches them once from PUBLIC_BASE_URL."""

import uuid
from zoneinfo import ZoneInfo

from app import clock, pdf
from app.config import ROOT, get_settings
from app.pricing import inr

OUT = ROOT / "static" / "cost_sheets"


def build(sheet: dict, project: dict, name: str) -> tuple[str, str]:
    """Returns (public url, local path)."""
    today = clock.utcnow().astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y")
    lines = [
        ("title", f"{project['name']} - {sheet['config']}"),
        ("body", f"{project['location']}  |  RERA {project['rera']}"),
        ("body", f"Prepared for {name or 'you'} on {today}"),
        ("gap", ""),
        ("h", f"Floor {sheet['floor']}  |  {sheet['carpet_sqft']} sq ft carpet  |  facing: {', '.join(sheet['facing'])}"),
        ("gap", ""),
    ]
    for label, amount in sheet["lines"]:
        lines.append(("mono", f"{label[:52]:<53}{inr(amount):>18}"))
    lines += [
        ("mono", "-" * 71),
        ("mono", f"{'All-inclusive estimate':<53}{inr(sheet['total']):>18}"),
        ("gap", ""),
        ("small", "Estimate only. Taxes and government charges as per prevailing rates; final figures in the"),
        ("small", "allotment letter. Offers and payment plans are confirmed by the sales manager."),
    ]
    name_part = uuid.uuid4().hex
    path = pdf.write(OUT / f"{name_part}.pdf", lines)
    url = f"{get_settings().public_base_url.rstrip('/')}/static/cost_sheets/{name_part}.pdf"
    return url, str(path)
