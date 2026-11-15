"""Place one outbound call through Ringg.

    RINGG_API_KEY=... RINGG_AGENT_ID=... uv run python scripts/start_call.py +919876543210 "Harsh" skyline-heights

Optional: RINGG_FROM_NUMBER=+91... picks the caller ID (otherwise the agent's own outbound numbers are used).
Request body per docs.ringg.ai/api-reference/calling/individual-calls/initiate-individual-call."""

import os
import sys

import httpx

BASE = "https://prod-api.ringg.ai/ca/api/v0"


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    number, name = sys.argv[1], sys.argv[2]
    project = sys.argv[3] if len(sys.argv) > 3 else ""
    body = {
        "name": name,
        "mobile_number": number,
        "agent_id": os.environ["RINGG_AGENT_ID"],
        "custom_args_values": {
            "callee_name": name,
            "mobile_number": number,
            "project": project,
            "lead_source": "website enquiry",
        },
        "call_config": {
            "max_call_length": 480,
            "call_time": {"call_start_time": "10:00", "call_end_time": "19:00", "timezone": "Asia/Kolkata"},
        },
    }
    if os.environ.get("RINGG_FROM_NUMBER"):
        body["from_number"] = os.environ["RINGG_FROM_NUMBER"]
    r = httpx.post(f"{BASE}/calling/outbound/individual", json=body,
                   headers={"X-API-KEY": os.environ["RINGG_API_KEY"]}, timeout=30)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
