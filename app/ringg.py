"""Ringg outbound calls. The cadence engine owns retries and timing, so Ringg's own retry
config is switched off per call; otherwise both would redial and break the call limits.
Docs: docs.ringg.ai/api-reference/calling/individual-calls/initiate-individual-call"""

import uuid

import httpx

from app import db
from app.config import get_settings


async def start_call(*, phone: str, name: str, flow: str, custom_args: dict) -> dict:
    s = get_settings()
    args = {"callee_name": name or "there", "mobile_number": phone, "flow": flow, **custom_args}
    args = {k: str(v) for k, v in args.items() if v is not None}
    body = {
        "name": name or "there",
        "mobile_number": phone,
        "agent_id": s.agent_id(flow),
        "custom_args_values": args,
        "call_config": {
            "max_call_length": 480,
            "call_retry_config": {"retry_count": 0},
            "call_time": {
                "call_start_time": f"{s.call_window_start:02d}:00",
                "call_end_time": f"{s.call_window_end:02d}:00",
                "timezone": "Asia/Kolkata",
            },
        },
        "call_category": flow,
    }
    if s.ringg_from_number:
        body["from_number"] = s.ringg_from_number

    if not s.live:
        call_id = f"mock-{uuid.uuid4().hex[:12]}"
        db.log_message(call_id, phone, "voice", flow, phone, "mocked", provider_id=call_id, payload=body)
        return {"ok": True, "call_id": call_id}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{s.ringg_base_url}/calling/outbound/individual", json=body,
                                  headers={"X-API-KEY": s.ringg_api_key})
        r.raise_for_status()
        call_id = r.json()["data"]["call_id"]
        db.log_message(call_id, phone, "voice", flow, phone, "sent", provider_id=call_id, payload=body)
        return {"ok": True, "call_id": call_id}
    except (httpx.HTTPError, KeyError) as e:
        detail = e.response.text if isinstance(e, httpx.HTTPStatusError) else str(e)
        db.log_message(None, phone, "voice", flow, phone, "failed", error=detail[:500], payload=body)
        return {"ok": False, "error": detail[:200]}
