"""Normalise leads from different sources into one shape.

Supported:
- generic JSON (website forms, Zapier): {name, phone, email, project, source, campaign, cp_id}
- Meta Lead Ads lead object (as returned by GET /{leadgen_id}): {field_data: [{name, values}], campaign_name, ...}
- portal exports with their own field names (mobile, contact_number, project_name, ...)"""

import re

from app.projects import find_project

ALIASES = {
    "name": ["name", "full_name", "fullname", "customer_name", "lead_name"],
    "phone": ["phone", "phone_number", "mobile", "mobile_number", "contact", "contact_number"],
    "email": ["email", "email_address"],
    "project": ["project", "project_name", "property", "property_name", "propertyname"],
    "source": ["source", "lead_source", "portal"],
    "campaign": ["campaign", "campaign_name", "ad_name", "utm_campaign"],
    "cp_id": ["cp_id", "channel_partner", "broker_id"],
}


SPOKEN_SOURCES = {
    "meta_lead_ads": "a Facebook or Instagram ad", "website": "our website", "99acres": "99acres",
    "magicbricks": "MagicBricks", "housing": "Housing.com", "google_ads": "a Google ad",
    "whatsapp_inbound": "WhatsApp", "referral": "a friend's referral", "walk_in": "our site office",
}


def spoken_source(source: str | None) -> str:
    """How the agent says where the enquiry came from ("you'd checked it out on ...")."""
    return SPOKEN_SOURCES.get((source or "").lower(), "our website")


def normalize_phone(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if raw and raw.strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits  # international (NRI) numbers
    return None


def normalize(body: dict) -> dict:
    flat = {k.lower(): v for k, v in body.items() if not isinstance(v, (list, dict))}
    for field in body.get("field_data", []):
        values = field.get("values") or [""]
        flat[field["name"].lower()] = values[0]

    lead = {}
    for key, names in ALIASES.items():
        lead[key] = next((str(flat[n]).strip() for n in names if flat.get(n)), None)

    lead["phone"] = normalize_phone(lead["phone"])
    if not lead["source"]:
        lead["source"] = "meta_lead_ads" if "field_data" in body else "website"
    project = find_project(lead["project"])
    lead["project_id"] = project["id"] if project else None
    return lead
