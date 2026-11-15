"""Persona evals: Claude plays the speed-to-lead agent (same prompt and tools as on Ringg),
another Claude plays a caller persona, and a third grades the transcript against a rubric.
Tools run against this service in-process (mock delivery, throwaway database), so each run
also yields hard checks: was the visit actually booked, was the cost sheet actually sent.

    uv run --group evals python -m evals.run                      # all personas
    uv run --group evals python -m evals.run --persona stop_calling --runs 3
    uv run --group evals python -m evals.run --dry-run            # render prompt + exercise tools, no API calls

Every run calls the Claude API and costs money: roughly 3 models x ~15 turns per persona.
Text stands in for audio, so this tests conversation logic and tool use, not speech recognition or latency."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="re-evals-")
os.environ.update(DB_PATH=f"{_tmp}/evals.db", DELIVERY_MODE="mock", SCHEDULER_ENABLED="false",
                  TOOL_SECRET="eval", INTAKE_SECRET="eval", WEBHOOK_SECRET="eval",
                  PUBLIC_BASE_URL="https://example.invalid")

import anthropic  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app import clock  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.intake import spoken_source  # noqa: E402
from app.main import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2030, 3, 6, 11, 0, tzinfo=IST)  # Wednesday morning: inside calling hours, managers on duty
MODEL = get_settings().eval_model
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
MAX_TURNS = 14
H = {"X-Tool-Secret": "eval"}

# ---------- the agent's tools (only the fields the LLM fills; Ringg fills the rest from call variables) ----------

def _tool(name, description, props, required):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


S = {"type": "string"}
TOOLS = [
    _tool("project_info", "Facts about a project: configurations, prices, possession, amenities, connectivity.",
          {"project": S}, ["project"]),
    _tool("check_availability", "Available unit types for a project (or all projects), whether they fit the budget, and in-budget alternatives.",
          {"project": S, "bhk": S, "budget": {**S, "description": "as the caller said it, e.g. '1.2 crore'"}}, ["bhk"]),
    _tool("send_cost_sheet", "Compute the all-inclusive price for a configuration and floor and send the cost sheet PDF on WhatsApp.",
          {"project": S, "bhk": S, "floor": {"type": "integer"}}, ["project", "bhk"]),
    _tool("send_brochure", "Send the project brochure on WhatsApp, email or both.",
          {"project": S, "channel": {"type": "string", "enum": ["whatsapp", "email", "both"]}, "email": S},
          ["project", "channel"]),
    _tool("book_site_visit", "Book (or reschedule) a site visit.",
          {"project": S, "date": {**S, "description": "YYYY-MM-DD"}, "time": {**S, "description": "HH:MM 24-hour"},
           "attendees": S}, ["project", "date", "time"]),
    _tool("book_callback", "Book a callback at a time the caller chose; with_person true if a human should call.",
          {"date": S, "time": S, "with_person": {"type": "boolean"}, "reason": S}, ["date", "time"]),
    _tool("prepare_transfer", "Alert the sales manager before a live transfer. Only transfer if the result says so.",
          {"notes": S}, []),
    _tool("transfer_call", "Transfer the call to the sales manager. Only after prepare_transfer said to.", {}, []),
    _tool("end_call", "Hang up. Use when the conversation is over.", {}, []),
]
ENDPOINT = {"project_info": "project-info", "check_availability": "check-availability",
            "send_cost_sheet": "send-cost-sheet", "send_brochure": "send-brochure",
            "book_site_visit": "book-site-visit", "book_callback": "book-callback",
            "prepare_transfer": "prepare-transfer"}


class Call:
    """One simulated call: the service, the lead, and everything the agent did."""

    def __init__(self, client: TestClient, persona: dict, phone: str):
        self.c, self.persona, self.phone = client, persona, phone
        self.call_id = f"eval-{persona['id']}-{phone[-4:]}"
        self.name = persona["lead"]["name"]
        self.tool_log: list[dict] = []

    def run_tool(self, name: str, args: dict) -> str:
        if name in ("end_call", "transfer_call"):
            return "ok"
        body = dict(args)
        if name not in ("project_info", "check_availability"):
            body |= {"call_id": self.call_id, "phone": self.phone}
        if name in ("send_cost_sheet", "send_brochure", "book_site_visit"):
            body["name"] = self.name
        r = self.c.post(f"/tools/{ENDPOINT[name]}", json=body, headers=H)
        result = r.json().get("result") if r.status_code == 200 else f"error {r.status_code}: {r.text[:200]}"
        self.tool_log.append({"tool": name, "input": args, "result": result})
        return result


def render_prompt(call: Call) -> str:
    lead = call.persona["lead"]
    ctx = call.c.post("/tools/lead-context", json={"phone": call.phone}, headers=H).json()
    project = next(p["name"] for p in json.loads((ROOT / "data/projects.json").read_text()) if p["id"] == lead["project"])
    variables = {"callee_name": call.name.split()[0], "mobile_number": call.phone, "project": project,
                 "project_id": lead["project"], "lead_source": spoken_source(lead["source"]), "known_budget": "", "known_bhk": "",
                 "known_timeline": "", "attempt": "1", "language": "", "flow": "speed_to_lead"}
    text = "\n\n".join((ROOT / p).read_text() for p in
                       ("ringg/agents/speed_to_lead.md", "ringg/agents/_core_rules.md",
                        "ringg/knowledge_base/objection_playbook.md"))
    text = re.sub(r"@\{\{(\w+)\}\}", lambda m: variables.get(m.group(1), ""), text)
    text = re.sub(r"@\(\(lead_context\.(\w+)\)\)", lambda m: str(ctx.get(m.group(1), "")), text)
    text = re.sub(r"@\|\|(\w+)\|\|", r"`\1`", text)
    harness = (f"This phone call is rendered as text. Everything you write is spoken aloud by a voice, so no "
               f"markdown, lists, emojis or stage directions. Today is {NOW:%A, %d %B %Y}, {NOW:%H:%M} IST. "
               "Your first reply is your opening line. Use end_call to hang up when the call is over.")
    return harness + "\n\n" + text


# ---------- the conversation ----------

def text_of(content) -> str:
    return " ".join(b.text for b in content if b.type == "text").strip()


def agent_turn(api: anthropic.Anthropic, system: str, messages: list, call: Call) -> tuple[str, str | None]:
    """Run the agent until it stops calling tools. Returns (what it said, end reason or None)."""
    spoken, ended = [], None
    while True:
        resp = api.beta.messages.create(model=MODEL, max_tokens=16000, system=system, tools=TOOLS,
                                        messages=messages, output_config={"effort": "medium"}, **FALLBACK)
        messages.append({"role": "assistant", "content": resp.content})
        if (t := text_of(resp.content)):
            spoken.append(t)
        if resp.stop_reason == "refusal":
            return " ".join(spoken), "refusal"
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            return " ".join(spoken), ended
        results = []
        for u in uses:
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": call.run_tool(u.name, u.input)})
            if u.name in ("end_call", "transfer_call"):
                ended = u.name
                call.tool_log.append({"tool": u.name, "input": {}, "result": "ok"})
        messages.append({"role": "user", "content": results})
        if ended:
            return " ".join(spoken), ended


def caller_turn(api: anthropic.Anthropic, persona: dict, history: list) -> str:
    system = (persona["persona"] + "\n\nYou are on a phone call with a property company's assistant. Reply with only "
              "what you would say out loud, one to three short sentences, the way a real person talks on the phone. "
              "Stay in character; don't make things easy for the assistant. When you would hang up, end your line "
              "with [HANGUP].")
    resp = api.beta.messages.create(model=MODEL, max_tokens=4000, system=system, messages=history,
                                    output_config={"effort": "low"}, **FALLBACK)
    return text_of(resp.content) or "[HANGUP]"


def simulate(api: anthropic.Anthropic, call: Call) -> list[dict]:
    system = render_prompt(call)
    agent_msgs = [{"role": "user", "content": "[Call connected. The callee says:] Hello?"}]
    caller_msgs: list[dict] = []
    transcript = [{"who": "caller", "text": "Hello?"}]
    for _ in range(MAX_TURNS):
        said, ended = agent_turn(api, system, agent_msgs, call)
        if said:
            transcript.append({"who": "agent", "text": said})
        if ended:
            transcript.append({"who": "system", "text": ended})
            break
        caller_msgs.append({"role": "user", "content": said or "(silence)"})
        reply = caller_turn(api, call.persona, caller_msgs)
        caller_msgs.append({"role": "assistant", "content": reply})
        transcript.append({"who": "caller", "text": reply.replace("[HANGUP]", "").strip()})
        if "[HANGUP]" in reply:
            transcript.append({"who": "system", "text": "caller hung up"})
            break
        agent_msgs.append({"role": "user", "content": reply})
    return transcript


# ---------- grading ----------

class Check(BaseModel):
    name: str
    passed: bool
    evidence: str


class Scorecard(BaseModel):
    grounded_facts: int          # 0-2: every price/fact came from a tool result
    no_invented_offers: bool
    brevity: int                 # 0-2: short spoken turns
    one_question_at_a_time: int  # 0-2
    language_match: bool
    objection_handling: int      # 0-2 (2 if no objections arose and nothing was mishandled)
    closed_on_next_step: bool    # visit, callback, brochure or a clean exit, as fits the caller
    respected_caller: bool       # busy / opt-out / human requests honoured immediately
    expectation_checks: list[Check]
    must_not_violations: list[str]
    overall: int                 # 0-10
    summary: str


RUBRIC = """Grade this sales call by an AI voice agent for a real-estate developer in Pune.
Score strictly from the transcript and tool log. Integer scales are 0 (poor), 1 (mixed), 2 (good).
- grounded_facts: every price, availability, date or amenity the agent stated appears in a tool result. Any number that does not is a 0.
- no_invented_offers: false if the agent offered or hinted at any discount, freebie, special price, guaranteed return or loan approval.
- brevity: spoken turns are one or two short sentences, suitable for a phone call.
- one_question_at_a_time: the agent rarely stacks questions.
- language_match: the agent followed the caller's language (Hindi/English/Hinglish).
- objection_handling: objections were acknowledged once, answered honestly, and the call moved on.
- closed_on_next_step: the call ended with the right concrete next step for this caller.
- respected_caller: requests to stop, to call later or to speak to a person were followed at once, without further pitching.
- expectation_checks: one entry per expectation and per hard check listed below, with evidence.
- must_not_violations: quote each listed must-not the agent violated (empty if none).
- overall: 0-10 for how a strong human presales executive would rate this call."""


def grade(api: anthropic.Anthropic, persona: dict, transcript: list, tool_log: list, hard: dict) -> Scorecard:
    content = (f"{RUBRIC}\n\nCaller persona:\n{persona['persona']}\n\nExpectations: {json.dumps(persona['expect'])}\n"
               f"Hard checks from the system (true = happened): {json.dumps(hard)}\n"
               f"Must not: {json.dumps(persona['must_not'])}\n\nTranscript:\n"
               + "\n".join(f"{t['who'].upper()}: {t['text']}" for t in transcript)
               + "\n\nTool log:\n" + json.dumps(tool_log, indent=1, ensure_ascii=False))
    resp = api.messages.parse(model=MODEL, max_tokens=16000, output_config={"effort": "high"},
                              messages=[{"role": "user", "content": content}], output_format=Scorecard)
    return resp.parsed_output


def hard_checks(client: TestClient, call: Call) -> dict:
    msgs = [m for m in client.get("/admin/messages", params={"limit": 1000}).json() if m["phone"] == call.phone]
    kinds = {m["kind"] for m in msgs}
    tools = {t["tool"] for t in call.tool_log}
    return {
        "visit_booked": any(v["phone"] == call.phone for v in client.get("/admin/visits").json()),
        "cost_sheet_sent": "cost_sheet" in kinds,
        "brochure_sent": "brochure" in kinds or "details" in kinds,
        "callback_booked": "book_callback" in tools,
        "transfer_prepared": "prepare_transfer" in tools,
        "alternative_offered": any("Within budget" in (t["result"] or "") for t in call.tool_log),
        "ended_cleanly": bool(tools & {"end_call", "transfer_call"}),
    }


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", action="append", help="persona id (repeatable); default all")
    ap.add_argument("--runs", type=int, default=1, help="runs per persona (calls vary run to run)")
    ap.add_argument("--dry-run", action="store_true", help="render prompts and exercise tools without calling Claude")
    args = ap.parse_args()

    personas = json.loads((ROOT / "evals/personas.json").read_text())
    if args.persona:
        personas = [p for p in personas if p["id"] in args.persona]
    clock.freeze(NOW.astimezone(ZoneInfo("UTC")))
    results = []
    with TestClient(app) as client:
        n = 0
        for persona in personas:
            for run in range(args.runs):
                n += 1
                phone = f"+9198765{n:05d}"
                lead = persona["lead"]
                client.post("/leads/import", headers={"X-Intake-Secret": "eval"}, json={"stage": "new", "leads": [
                    {"phone": phone, "name": lead["name"], "project": lead["project"], "source": lead["source"]}]})
                call = Call(client, persona, phone)

                if args.dry_run:
                    print(render_prompt(call)[:1500], "...\n")
                    for name, a in [("check_availability", {"project": lead["project"], "bhk": "3", "budget": "1.2 crore"}),
                                    ("send_brochure", {"project": lead["project"], "channel": "whatsapp"}),
                                    ("book_site_visit", {"project": lead["project"], "date": "2030-03-09", "time": "11:00"})]:
                        print(f"  {name}: {call.run_tool(name, a)}")
                    print("  hard checks:", hard_checks(client, call))
                    break

                api = anthropic.Anthropic()
                transcript = simulate(api, call)
                hard = hard_checks(client, call)
                card = grade(api, persona, transcript, call.tool_log, hard)
                expected_ok = all(hard.get(k) == v for k, v in persona["expect"].items() if k in hard)
                results.append({"persona": persona["id"], "run": run, "hard": hard, "expected_ok": expected_ok,
                                "score": card.model_dump(), "transcript": transcript, "tools": call.tool_log})
                print(f"{persona['id']:<20} run {run}  overall {card.overall:>2}/10  "
                      f"expected {'ok ' if expected_ok else 'MISS'}  violations {len(card.must_not_violations)}  "
                      f"{card.summary[:80]}")

    if args.dry_run:
        return
    out = ROOT / "evals" / "results" / f"run-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "results": results}, indent=1, ensure_ascii=False))
    passed = sum(r["expected_ok"] and not r["score"]["must_not_violations"] for r in results)
    print(f"\n{passed}/{len(results)} calls met expectations with no violations. Details: {out}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
