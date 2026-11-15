"""Email over SMTP with the brochure PDF attached. Works with Gmail app passwords,
Amazon SES SMTP, Resend SMTP or any other SMTP relay."""

import asyncio
import smtplib
from email.message import EmailMessage
from html import escape
from pathlib import Path

from app import db
from app.config import get_settings


def _project_html(name: str, project: dict) -> str:
    rows = [
        ("Location", project["location"]),
        ("Configurations", ", ".join(project["configurations"])),
        ("Carpet area", project["carpet_area"]),
        ("Price", project["price_range"]),
        ("Status", f"{project['status']}, possession {project['possession']}"),
        ("Amenities", ", ".join(project["amenities"])),
        ("RERA", project["rera"]),
        ("Site visits", f"{project['site_address']} ({project['visit_hours']})"),
    ]
    table = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows
    )
    return (
        f"<p>Hi {escape(name or 'there')},</p>"
        f"<p>Thanks for speaking with us. Here are the details of <b>{escape(project['name'])}</b>. "
        f"The brochure is attached.</p><table>{table}</table>"
        "<p>Reply to this email or on WhatsApp to book a site visit.</p>"
    )


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def _deliver(msg: EmailMessage, *, call_id: str | None, phone: str, kind: str) -> dict:
    s = get_settings()
    meta = {"to": msg["To"], "subject": msg["Subject"]}
    if not s.live:
        db.log_message(call_id, phone, "email", kind, msg["To"], "mocked", payload=meta)
        return {"ok": True, "mode": "mock"}
    try:
        await asyncio.to_thread(_send_smtp, msg)
        db.log_message(call_id, phone, "email", kind, msg["To"], "sent", payload=meta)
        return {"ok": True}
    except (smtplib.SMTPException, OSError) as e:
        db.log_message(call_id, phone, "email", kind, msg["To"], "failed", error=str(e)[:500], payload=meta)
        return {"ok": False, "error": str(e)[:200]}


async def send_project_details(*, call_id: str | None, phone: str, to: str, name: str, project: dict, pdf: Path) -> dict:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"] = s.email_from, to
    msg["Subject"] = f"{project['name']}: brochure and details"
    msg.set_content(f"Hi {name or 'there'}, the {project['name']} brochure is attached.")
    msg.add_alternative(_project_html(name, project), subtype="html")
    if pdf.exists():
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    return await _deliver(msg, call_id=call_id, phone=phone, kind="details")


async def notify_sales(*, call_id: str | None, phone: str, subject: str, lines: dict, to: str | None = None) -> dict:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.email_from, to or s.sales_team_email, subject
    msg.set_content("\n".join(f"{k}: {v}" for k, v in lines.items() if v))
    return await _deliver(msg, call_id=call_id, phone=phone, kind="sales_alert")
