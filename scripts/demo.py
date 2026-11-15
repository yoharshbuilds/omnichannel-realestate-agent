"""Replay a realistic morning of presales traffic against a running server (mock mode),
so the dashboard and tables have something to show.

    uv run uvicorn app.main:app            # in one terminal (DELIVERY_MODE=mock)
    uv run python scripts/demo.py          # in another; then open /admin/dashboard

Calls are simulated by posting the webhook Ringg would send after each conversation."""

import os
import random
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://localhost:8000")
INTAKE = {"X-Intake-Secret": os.environ.get("INTAKE_SECRET", "change-me-intake")}
HOOK = {"Authorization": f"Bearer {os.environ.get('WEBHOOK_SECRET', 'change-me-webhook')}"}
TOOL = {"X-Tool-Secret": os.environ.get("TOOL_SECRET", "change-me-tools")}

LEADS = [
    ("Rahul Deshmukh", "skyline", "99acres", {"bhk_preference": "2 BHK", "budget": "1.1 crore", "purchase_timeline": "3 months", "decision_maker_present": "both", "wants_brochure": True}, "visit"),
    ("Pooja Nair", "palm grove", "meta_lead_ads", {"bhk_preference": "3 BHK", "budget": "1.2 crore", "purchase_timeline": "6 months", "objections": "price"}, None),
    ("Sunil Agarwal", "skyline", "magicbricks", {"bhk_preference": "3 BHK", "budget": "1.8 crore", "purchase_timeline": "1 month", "decision_maker_present": "yes", "loan_status": "self-funded"}, "visit"),
    ("Arjun Kapoor", "metro one", "housing", {"opt_out": True}, None),
    ("Meera Joshi", "metro one", "website", {"callback_requested": True}, None),
    ("Vikram Iyer", "palm grove", "website", {"bhk_preference": "4 BHK villa", "budget": "3.5 crore", "purchase_timeline": "next year", "purpose": "investment"}, None),
    ("Kavita Shah", "skyline", "referral", {"bhk_preference": "3 BHK", "budget": "1.9 crore", "purchase_timeline": "immediate", "decision_maker_present": "yes", "loan_status": "pre-approved"}, "visit"),
    ("Neha Kulkarni", "metro one", "meta_lead_ads", {}, "no_answer"),
    ("Amit Verma", "skyline", "99acres", {}, "no_answer"),
]


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=10)
    if c.get("/health").json().get("mode") != "mock":
        sys.exit("Refusing to run: the server is not in DELIVERY_MODE=mock.")

    for i, (name, project, source, analysis, outcome) in enumerate(LEADS):
        phone = f"+9199000{i:05d}"
        c.post("/leads/intake", headers=INTAKE, json={"name": name, "phone": phone, "project": project, "source": source})
        time.sleep(0.3)
        call = next(m for m in c.get("/admin/messages", params={"limit": 500}).json()
                    if m["phone"] == phone and m["channel"] == "voice")
        connected = outcome != "no_answer"
        c.post("/webhooks/ringg", headers=HOOK, json={
            "event_type": "all_processing_completed", "call_id": call["provider_id"], "status": "completed",
            "user_message_count": random.randint(6, 14) if connected else 0, "to_number": phone,
            "custom_args_values": {"mobile_number": phone, "callee_name": name.split()[0], "flow": "speed_to_lead"},
            "platform_analysis": {"summary": f"Demo call with {name}."},
            "client_analysis": {"customer_name": name, "interested_project": project, **analysis} if connected else {},
        })
        if outcome == "visit":
            r = c.post("/tools/book-site-visit", headers=TOOL, json={
                "call_id": call["provider_id"], "phone": phone, "name": name, "project": project,
                "date": time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400)), "time": "11:00"}).json()
            print(f"{name:<16} visit: {r['result'][:70]}")
        else:
            print(f"{name:<16} {'no answer' if not connected else 'call processed'}")

    visits = c.get("/admin/visits").json()
    if visits:
        c.post(f"/visits/{visits[-1]['id']}/outcome", headers=INTAKE, json={"status": "done"})
    print(f"\nDashboard: {BASE}/admin/dashboard")


if __name__ == "__main__":
    main()
