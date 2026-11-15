"""End-to-end playbooks in mock mode with a frozen clock (starts Wednesday 10:30 IST)."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.conftest import INTAKE, PHONE, START, payload_of

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent


def call_id_of(task_or_msg: dict) -> str:
    return task_or_msg["provider_id"]


# ---------- security ----------

def test_endpoints_need_secrets(env):
    assert env.c.post("/tools/project-info", json={"project": "x"}).status_code == 401
    assert env.c.post("/webhooks/ringg", json={}).status_code == 401
    assert env.c.post("/leads/intake", json={"phone": PHONE}).status_code == 401


def test_whatsapp_verification_handshake(env):
    ok = env.c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "v-token",
                                                  "hub.challenge": "42"})
    bad = env.c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                   "hub.challenge": "42"})
    assert ok.text == "42" and bad.status_code == 403


# ---------- speed to lead ----------

def test_new_lead_is_called_immediately_then_whatsapp(env):
    r = env.intake(name="Harsh Shah", project="skyline", source="99acres")
    assert r["action"] == "speed_to_lead_started"
    assert len(env.calls()) == 1  # intake triggers the first tick itself
    call = payload_of(env.calls()[0])
    assert call["custom_args_values"]["flow"] == "speed_to_lead"
    assert call["custom_args_values"]["project"] == "Skyline Heights"
    assert call["call_config"]["call_retry_config"] == {"retry_count": 0}
    lead = env.lead()
    assert lead["stage"] == "attempting" and lead["first_call_at"] == lead["created_at"]

    env.advance(minutes=1)
    env.tick()
    brochure = env.sent("whatsapp", "brochure")
    assert len(brochure) == 1 and "skyline-heights.pdf" in brochure[0]["payload"]


def test_unanswered_ladder_and_answered_stops_it(env):
    env.intake(name="Harsh", project="metro one")
    first = call_id_of(env.calls()[0])
    env.call_done(first, user_turns=0)  # voicemail / silence: not a conversation
    assert env.lead()["stage"] == "attempting"

    env.advance(minutes=30)
    env.tick()
    assert len(env.calls()) == 2

    second = call_id_of(env.calls()[1])
    env.call_done(second, bhk_preference="2 BHK", budget="90 lakh", purchase_timeline="3 months")
    lead = env.lead()
    assert lead["stage"] == "qualified" and lead["first_connect_at"] and lead["score"] > 0
    assert not [t for t in env.pending() if t["flow"] == "speed_to_lead"]

    env.advance(hours=5)
    env.tick()
    assert len(env.calls()) == 2  # ladder stopped


def test_night_lead_waits_for_calling_hours(env):
    env.at(datetime(2030, 3, 6, 22, 15, tzinfo=IST))
    env.intake(name="Night Owl", project="skyline")
    assert env.calls() == []
    (first,) = env.pending()
    assert datetime.fromisoformat(first["run_at"]).astimezone(IST).hour == 10

    env.at(datetime(2030, 3, 7, 10, 0, tzinfo=IST))
    env.tick()
    assert len(env.calls()) == 1
    env.advance(minutes=1)
    env.tick()
    assert len(env.sent("whatsapp", "brochure")) == 1
    second = next(t for t in env.pending() if t["kind"] == "call")
    assert datetime.fromisoformat(second["run_at"]).astimezone(IST).strftime("%H:%M") == "10:30"


def test_daily_call_limit(env):
    from app import cadence
    env.intake(name="Harsh", project="skyline")
    now = datetime(2030, 3, 6, 10, 30, tzinfo=IST)
    for minutes in (5, 10, 15):
        cadence.schedule_callback(PHONE, now + timedelta(minutes=minutes))
    env.advance(minutes=20)
    env.tick()
    assert len(env.calls()) == 3  # intake call + 2 callbacks; the 3rd callback waits
    held = [t for t in env.pending() if t["flow"] == "callback"]
    assert len(held) == 1 and "daily call limit" in held[0]["result"]


def test_opt_out_on_call_stops_everything(env):
    env.intake(name="Harsh", project="skyline")
    env.call_done(call_id_of(env.calls()[0]), opt_out=True)
    lead = env.lead()
    assert lead["opted_out"] == 1 and lead["stage"] == "opted_out"
    assert env.pending() == []
    assert env.intake(name="Harsh", project="skyline")["action"] == "ignored_opted_out"


def test_duplicate_webhook_processed_once(env):
    env.intake(name="Harsh", project="skyline")
    cid = call_id_of(env.calls()[0])
    env.call_done(cid, wants_brochure=True, interested_project="Skyline Heights")
    assert env.call_done(cid, wants_brochure=True)["duplicate"] is True
    assert len(env.sent("whatsapp", "brochure")) == 1


def test_callback_request_is_scheduled(env):
    env.intake(name="Harsh", project="skyline")
    env.call_done(call_id_of(env.calls()[0]), callback_requested=True)
    cb = [t for t in env.pending() if t["flow"] == "callback"]
    assert len(cb) == 1


def test_long_timeline_goes_to_nurture(env):
    env.intake(name="Harsh", project="palm grove")
    env.call_done(call_id_of(env.calls()[0]), bhk_preference="4 BHK villa", purchase_timeline="next year")
    assert env.lead()["stage"] == "nurture"


# ---------- tools ----------

def test_lead_context_tool(env):
    env.intake(name="Harsh", project="skyline")
    env.call_done(call_id_of(env.calls()[0]), bhk_preference="3 BHK", budget="1.8 crore")
    ctx = env.tool("lead-context", phone=PHONE)
    assert "Skyline Heights" in ctx["known"] and "3 BHK" in ctx["known"] and "1.8 crore" in ctx["known"]
    assert ctx["manager_phone"].startswith("+91") and ctx["manager_on_duty"] == "yes"


def test_check_availability_and_cost_sheet(env):
    r = env.tool("check-availability", project="skyline", bhk="3", budget="1.5 crore")
    assert "above their budget" in r["result"]

    r = env.tool("send-cost-sheet", call_id="c1", phone=PHONE, name="Harsh", project="skyline", bhk="3 BHK", floor=12)
    assert r["sent"] and "1.94 crore" in r["result"]
    msg = env.sent("whatsapp", "cost_sheet")[0]
    url = payload_of(msg)["template"]["components"][0]["parameters"][0]["document"]["link"]
    pdf_path = ROOT / "static" / url.split("/static/")[1]
    assert pdf_path.read_bytes().startswith(b"%PDF")
    pdf_path.unlink()


def test_send_brochure_both_channels_and_bad_email(env):
    r = env.tool("send-brochure", call_id="c1", phone=PHONE, name="Harsh", project="Skyline Heights",
                 channel="both", email="harsh dot shah at gmail dot com")
    assert r["sent"] and "harsh.shah@gmail.com" in r["result"]
    r = env.tool("send-brochure", call_id="c2", phone=PHONE, project="metro", channel="email", email="harsh at gmail")
    assert not r["sent"] and "spell" in r["result"]


def test_transfer_needs_a_manager_on_duty(env):
    env.intake(name="Harsh", project="skyline")
    r = env.tool("prepare-transfer", call_id="c1", phone=PHONE, notes="wants to negotiate")
    assert r["transfer"] is True
    alert = env.sent("whatsapp", "manager_alert")[-1]
    assert "Incoming transfer" in alert["payload"] and "wants to negotiate" in alert["payload"]

    env.at(datetime(2030, 3, 6, 21, 0, tzinfo=IST))
    r = env.tool("prepare-transfer", call_id="c1", phone=PHONE)
    assert r["transfer"] is False and "book_callback" in r["result"]


def test_book_callback_shifts_into_calling_hours(env):
    r = env.tool("book-callback", call_id="c1", phone=PHONE, date="2030-03-06", time="21:30")
    assert "10:00 AM" in r["result"]


# ---------- visit concierge ----------

def book_visit(env, date="2030-03-09", time="11:00", project="skyline") -> dict:
    return env.tool("book-site-visit", call_id="c-visit", phone=PHONE, name="Harsh", project=project,
                    date=date, time=time, attendees="Harsh and wife")


def test_visit_booking_runs_concierge(env):
    env.intake(name="Harsh", project="skyline")
    r = book_visit(env)  # Saturday 11:00
    assert r["booked"]
    vid = r["visit_id"]
    assert env.lead()["stage"] == "visit_booked"
    assert len(env.sent("whatsapp", "visit_confirmation")) == 1
    flows = {t["flow"] for t in env.pending()}
    assert f"visit:{vid}" in flows and "speed_to_lead" not in flows

    env.at(datetime(2030, 3, 8, 11, 0, tzinfo=IST))  # T-24h
    env.tick()
    reminder = env.sent("whatsapp", "visit_reminder")
    assert len(reminder) == 1 and f"VISIT_CONFIRM:{vid}" in reminder[0]["payload"]

    assert env.wa_reply(payload=f"VISIT_CONFIRM:{vid}")["handled"] == ["visit_confirmed"]
    calls_before = len(env.calls())
    env.at(datetime(2030, 3, 8, 15, 0, tzinfo=IST))  # T-20h: confirmed, so no reconfirm call
    env.tick()
    assert len(env.calls()) == calls_before

    env.at(datetime(2030, 3, 9, 9, 0, tzinfo=IST))  # T-2h
    env.tick()
    loc = env.sent("whatsapp", "visit_location")
    assert len(loc) == 1 and '"latitude": 18.559' in loc[0]["payload"]


def test_unconfirmed_visit_gets_reconfirm_call(env):
    vid = book_visit(env)["visit_id"]
    env.at(datetime(2030, 3, 8, 15, 0, tzinfo=IST))
    env.tick()
    call = payload_of(env.calls()[-1])
    assert call["custom_args_values"]["flow"] == "visit_reconfirm"
    assert call["custom_args_values"]["visit_id"] == str(vid)
    assert "Saturday" in call["custom_args_values"]["visit_time"]

    env.call_done(env.calls()[-1]["provider_id"], flow="visit_reconfirm", extra_args={"visit_id": str(vid)},
                  visit_confirmed=True)
    assert next(v for v in env.table("visits") if v["id"] == vid)["status"] == "confirmed"


def test_reschedule_retires_old_visit(env):
    first = book_visit(env)["visit_id"]
    second = book_visit(env, date="2030-03-10", time="16:00")["visit_id"]
    visits = {v["id"]: v["status"] for v in env.table("visits")}
    assert visits[first] == "rescheduled" and visits[second] == "booked"
    assert not [t for t in env.pending() if t["flow"] == f"visit:{first}"]


def test_closed_day_and_hours_are_enforced(env):
    r = book_visit(env, date="2030-03-12", project="palm grove")  # a Tuesday
    assert not r["booked"] and "closed" in r["result"]
    r = book_visit(env, time="21:00")
    assert not r["booked"] and "between" in r["result"]


def test_visit_done_triggers_feedback_then_negotiation(env):
    env.intake(name="Harsh", project="skyline")
    vid = book_visit(env)["visit_id"]
    env.at(datetime(2030, 3, 9, 12, 30, tzinfo=IST))
    env.c.post(f"/visits/{vid}/outcome", json={"status": "done"}, headers=INTAKE)
    assert env.lead()["stage"] == "visit_done"

    env.advance(hours=3)
    env.tick()
    call = env.calls()[-1]
    assert payload_of(call)["custom_args_values"]["flow"] == "post_visit_feedback"

    env.call_done(call["provider_id"], flow="post_visit_feedback", next_step="negotiation",
                  visit_feedback="loved the clubhouse, wants a better floor")
    assert env.lead()["stage"] == "negotiation"
    assert "ready to negotiate" in env.sent("whatsapp", "manager_alert")[-1]["payload"]


def test_no_show_gets_recovery_call(env):
    vid = book_visit(env)["visit_id"]
    env.at(datetime(2030, 3, 9, 12, 0, tzinfo=IST))
    env.c.post(f"/visits/{vid}/outcome", json={"status": "no_show"}, headers=INTAKE)
    env.advance(hours=1)
    env.tick()
    assert payload_of(env.calls()[-1])["custom_args_values"]["flow"] == "visit_recovery"


# ---------- WhatsApp replies ----------

def test_whatsapp_stop_and_free_text(env):
    env.intake(name="Harsh", project="skyline")
    other = "+919800000002"
    env.intake(name="Asha", project="metro one", phone=other)

    assert env.wa_reply(text="STOP")["handled"] == ["opt_out"]
    assert env.lead()["opted_out"] == 1 and env.pending() == []

    assert env.wa_reply(text="Is there a 2 BHK with balcony?", phone=other)["handled"] == ["free_text"]
    assert env.lead(other)["engaged_at"]
    assert [t for t in env.pending(other) if t["flow"] == "callback"]
    assert env.sent("whatsapp", "manager_alert", phone=other)


def test_whatsapp_duplicate_delivery_ignored(env):
    env.intake(name="Harsh", project="skyline")
    env.wa_reply(payload="CALL_LATER", msg_id="wamid.same")
    assert env.wa_reply(payload="CALL_LATER", msg_id="wamid.same")["handled"] == []


# ---------- campaigns & dashboard ----------

def test_reactivation_campaign(env):
    old = (START - timedelta(days=200)).isoformat()
    env.c.post("/leads/import", headers=INTAKE, json={"leads": [
        {"phone": "9800000011", "name": "Old One", "project": "skyline", "last_contact_at": old},
        {"phone": "9800000012", "name": "Old Two", "project": "metro", "last_contact_at": old},
        {"phone": "bad"},
    ]})
    r = env.c.post("/campaigns/reactivation", headers=INTAKE, json={"older_than_days": 90, "spacing_seconds": 0})
    assert r.json()["scheduled"] == 2
    env.tick()
    flows = {payload_of(m)["custom_args_values"]["flow"] for m in env.table("messages") if m["channel"] == "voice"}
    assert flows == {"reactivation"}


def test_nurture_campaign_and_reply_to_it(env):
    env.c.post("/leads/import", headers=INTAKE, json={"leads": [{"phone": PHONE, "name": "Harsh", "project": "palm"}]})
    env.c.post("/campaigns/nurture", headers=INTAKE, json={"update": "Two corner villas just opened"})
    env.tick()
    assert "Two corner villas" in env.sent("whatsapp", "nurture")[0]["payload"]
    assert env.wa_reply(payload="CALL_ME")["handled"] == ["call_me"]
    env.advance(minutes=3)
    env.tick()
    assert payload_of(env.calls()[-1])["custom_args_values"]["flow"] == "callback"


def test_dashboard_and_funnel(env):
    env.intake(name="Harsh", project="skyline", source="99acres")
    env.call_done(call_id_of(env.calls()[0]), bhk_preference="3 BHK", budget="2 crore", purchase_timeline="1 month")
    book_visit(env)
    f = env.c.get("/admin/funnel").json()
    assert f["leads"] == 1 and f["called"] == 1 and f["connected"] == 1
    assert f["qualified"] == 1 and f["visit_booked"] == 1 and f["median_seconds_to_first_call"] == 0
    html = env.c.get("/admin/dashboard").text
    assert "Presales funnel" in html and "99acres" in html
