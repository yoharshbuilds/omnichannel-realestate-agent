import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.config import get_settings

IST = ZoneInfo("Asia/Kolkata")
START = datetime(2030, 3, 6, 10, 30, tzinfo=IST)  # a Wednesday morning
TOOL = {"X-Tool-Secret": "t-secret"}
HOOK = {"Authorization": "Bearer w-secret"}
INTAKE = {"X-Intake-Secret": "i-secret"}
PHONE = "+919800000001"


class Env:
    def __init__(self, client: TestClient):
        self.c = client

    # time
    def at(self, dt: datetime) -> None:
        clock.freeze(dt)

    def advance(self, **kw) -> None:
        clock.freeze(clock.utcnow() + timedelta(**kw))

    def tick(self) -> int:
        return self.c.post("/admin/tick").json()["ran"]

    # reads
    def table(self, name: str) -> list[dict]:
        return self.c.get(f"/admin/{name}", params={"limit": 500}).json()

    def lead(self, phone: str = PHONE) -> dict:
        return next(r for r in self.table("leads") if r["phone"] == phone)

    def sent(self, channel: str | None = None, kind: str | None = None, phone: str = PHONE) -> list[dict]:
        return [m for m in reversed(self.table("messages"))
                if m["phone"] == phone and (channel is None or m["channel"] == channel)
                and (kind is None or m["kind"] == kind) and m["status"] in ("mocked", "sent")]

    def calls(self, phone: str = PHONE) -> list[dict]:
        return self.sent("voice", phone=phone)

    def pending(self, phone: str = PHONE) -> list[dict]:
        return [t for t in self.table("tasks") if t["phone"] == phone and t["status"] == "pending"]

    # writes
    def intake(self, **body) -> dict:
        body.setdefault("phone", PHONE)
        return self.c.post("/leads/intake", json=body, headers=INTAKE).json()

    def tool(self, _tool: str, **body) -> dict:
        return self.c.post(f"/tools/{_tool}", json=body, headers=TOOL).json()

    def call_done(self, call_id: str, flow: str = "speed_to_lead", user_turns: int = 6, phone: str = PHONE,
                  extra_args: dict | None = None, **analysis) -> dict:
        payload = {
            "event_type": "all_processing_completed", "call_id": call_id, "status": "completed",
            "user_message_count": user_turns, "to_number": phone,
            "custom_args_values": {"mobile_number": phone, "callee_name": "Harsh", "flow": flow, **(extra_args or {})},
            "platform_analysis": {"summary": "test call"},
            "client_analysis": analysis,
        }
        return self.c.post("/webhooks/ringg", json=payload, headers=HOOK).json()

    def wa_reply(self, text: str = "", payload: str = "", phone: str = PHONE, msg_id: str | None = None) -> dict:
        m = {"from": phone.lstrip("+"), "id": msg_id or f"wamid.{datetime.now().timestamp()}"}
        if payload:
            m |= {"type": "button", "button": {"text": text or payload, "payload": payload}}
        else:
            m |= {"type": "text", "text": {"body": text}}
        body = {"entry": [{"changes": [{"value": {"messages": [m]}}]}]}
        return self.c.post("/webhooks/whatsapp", json=body).json()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in {"DB_PATH": str(tmp_path / "t.db"), "TOOL_SECRET": "t-secret", "WEBHOOK_SECRET": "w-secret",
                 "INTAKE_SECRET": "i-secret", "DELIVERY_MODE": "mock", "SCHEDULER_ENABLED": "false",
                 "WA_VERIFY_TOKEN": "v-token"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    clock.freeze(START)
    from app.main import app
    with TestClient(app) as c:
        yield Env(c)
    clock.freeze(None)
    get_settings.cache_clear()


def payload_of(message: dict) -> dict:
    return json.loads(message["payload"])
