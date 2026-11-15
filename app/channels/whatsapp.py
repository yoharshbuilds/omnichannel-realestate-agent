"""WhatsApp Cloud API (Meta). Outside the 24-hour customer-service window only approved
templates can be sent, so every outbound message here is a template (ringg/whatsapp_templates.md).
Quick-reply buttons carry a payload such as "VISIT_CONFIRM:12" that comes back on the inbound
webhook, so a tap maps straight to an action.

In mock mode nothing leaves the machine; the request body is logged to the messages table."""

import httpx

from app import db
from app.config import get_settings


def to_wa_number(phone: str) -> str:
    """Ringg sends +91XXXXXXXXXX; the Cloud API wants digits only."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "91" + digits if len(digits) == 10 else digits


def from_wa_number(wa_id: str) -> str:
    return "+" + wa_id if not wa_id.startswith("+") else wa_id


def _template(name: str, to: str, body: list[str], header: dict | None = None, buttons: list[str] | None = None) -> dict:
    s = get_settings()
    components = []
    if header:
        components.append({"type": "header", "parameters": [header]})
    components.append({"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in body]})
    for i, payload in enumerate(buttons or []):
        components.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                           "parameters": [{"type": "payload", "payload": payload}]})
    return {
        "messaging_product": "whatsapp",
        "to": to_wa_number(to),
        "type": "template",
        "template": {"name": name, "language": {"code": s.wa_template_lang}, "components": components},
    }


async def _send(payload: dict, *, call_id: str | None, phone: str, kind: str) -> dict:
    s = get_settings()
    if not s.live:
        db.log_message(call_id, phone, "whatsapp", kind, payload["to"], "mocked", payload=payload)
        return {"ok": True, "mode": "mock"}

    url = f"https://graph.facebook.com/{s.wa_api_version}/{s.wa_phone_number_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {s.wa_access_token}"})
        r.raise_for_status()
        msg_id = r.json()["messages"][0]["id"]
        db.log_message(call_id, phone, "whatsapp", kind, payload["to"], "sent", provider_id=msg_id, payload=payload)
        return {"ok": True, "id": msg_id}
    except (httpx.HTTPError, KeyError, IndexError) as e:
        detail = e.response.text if isinstance(e, httpx.HTTPStatusError) else str(e)
        db.log_message(call_id, phone, "whatsapp", kind, payload["to"], "failed", error=detail[:500], payload=payload)
        return {"ok": False, "error": detail[:200]}


def _doc(url: str, filename: str) -> dict:
    return {"type": "document", "document": {"link": url, "filename": filename}}


async def send_brochure(*, call_id: str | None, phone: str, name: str, project: dict, url: str) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_brochure, phone, [name or "there", project["name"]],
                        header=_doc(url, f"{project['name']} brochure.pdf"))
    return await _send(payload, call_id=call_id, phone=phone, kind="brochure")


async def send_details(*, call_id: str | None, phone: str, name: str, project: dict, details: str) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_details, phone, [name or "there", project["name"], details])
    return await _send(payload, call_id=call_id, phone=phone, kind="details")


async def send_cost_sheet(*, call_id: str | None, phone: str, name: str, project: dict, label: str,
                          total: str, url: str) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_cost_sheet, phone, [name or "there", f"{project['name']} {label}", total],
                        header=_doc(url, f"{project['name']} {label} cost sheet.pdf"))
    return await _send(payload, call_id=call_id, phone=phone, kind="cost_sheet")


async def send_visit_confirmation(*, call_id: str | None, phone: str, name: str, project: dict, when: str) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_visit, phone, [name or "there", project["name"], when, project["site_address"]])
    return await _send(payload, call_id=call_id, phone=phone, kind="visit_confirmation")


async def send_visit_reminder(*, phone: str, name: str, project: dict, when: str, visit_id: int) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_visit_reminder, phone, [name or "there", project["name"], when],
                        buttons=[f"VISIT_CONFIRM:{visit_id}", f"VISIT_RESCHEDULE:{visit_id}"])
    return await _send(payload, call_id=None, phone=phone, kind="visit_reminder")


async def send_visit_location(*, phone: str, name: str, project: dict, when: str, manager: dict) -> dict:
    s = get_settings()
    header = {"type": "location", "location": {
        "latitude": project["lat"], "longitude": project["lng"],
        "name": project["name"], "address": project["site_address"],
    }}
    payload = _template(s.wa_template_visit_location, phone,
                        [name or "there", when, manager["name"], manager["phone"]], header=header)
    return await _send(payload, call_id=None, phone=phone, kind="visit_location")


async def send_stop_check(*, phone: str, name: str, project: dict) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_stop_check, phone, [name or "there", project["name"]],
                        buttons=["SEND_DETAILS", "CALL_LATER", "STOP"])
    return await _send(payload, call_id=None, phone=phone, kind="stop_check")


async def send_nurture(*, phone: str, name: str, update: str) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_nurture, phone, [name or "there", update], buttons=["CALL_ME", "STOP"])
    return await _send(payload, call_id=None, phone=phone, kind="nurture")


async def send_manager_alert(*, manager: dict, lead_phone: str, summary: str, call_id: str | None = None) -> dict:
    s = get_settings()
    payload = _template(s.wa_template_manager_alert, manager["phone"], [manager["name"], lead_phone, summary[:900]])
    return await _send(payload, call_id=call_id, phone=lead_phone, kind="manager_alert")


async def send_text(*, phone: str, text: str, kind: str = "reply") -> dict:
    """Free text: only valid within 24 hours of the lead's last message, i.e. as a reply."""
    payload = {"messaging_product": "whatsapp", "to": to_wa_number(phone), "type": "text", "text": {"body": text}}
    return await _send(payload, call_id=None, phone=phone, kind=kind)


def parse_inbound(body: dict) -> list[dict]:
    """Flatten a Cloud API webhook into [{phone, id, type, text, payload}]. Status callbacks are ignored."""
    out = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                kind = m.get("type")
                text, payload = "", ""
                if kind == "text":
                    text = m["text"]["body"]
                elif kind == "button":
                    text, payload = m["button"].get("text", ""), m["button"].get("payload", "")
                elif kind == "interactive":
                    reply = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    text, payload = reply.get("title", ""), reply.get("id", "")
                out.append({"phone": from_wa_number(m["from"]), "id": m["id"], "type": kind,
                            "text": text, "payload": payload, "raw": m})
    return out
