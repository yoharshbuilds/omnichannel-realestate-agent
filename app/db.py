"""SQLite storage.

leads           one row per phone number: profile, requirement, score, stage, owner
visits          site visits and their status
tasks           the cadence engine's queue (calls and WhatsApp messages due at a time)
messages        every outbound call / WhatsApp / email, mocked or real
inbound         WhatsApp messages received from leads
webhook_events  Ringg events, keyed for idempotency (Ringg may deliver an event more than once)

All timestamps are stored as UTC ISO strings so they sort and compare as text."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app import clock
from app.config import get_settings

# Lead stages, in funnel order. "nurture", "lost" and "opted_out" sit outside the main line.
STAGES = ["new", "attempting", "contacted", "qualified", "visit_booked", "visit_done", "negotiation", "booked"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    phone TEXT PRIMARY KEY,
    name TEXT, email TEXT, language TEXT,
    source TEXT, campaign TEXT, cp_id TEXT,
    project_id TEXT, budget TEXT, bhk TEXT, preferred_location TEXT,
    timeline TEXT, purpose TEXT, loan_status TEXT, decision_maker TEXT,
    objections TEXT,
    score INTEGER, lead_temperature TEXT,
    stage TEXT DEFAULT 'new', lost_reason TEXT,
    site_visit_at TEXT, callback_requested INTEGER DEFAULT 0,
    manager_id TEXT, opted_out INTEGER DEFAULT 0,
    engaged_at TEXT, first_call_at TEXT, first_connect_at TEXT,
    last_call_id TEXT, last_contact_at TEXT, summary TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT, name TEXT, project_id TEXT, visit_at TEXT, call_id TEXT,
    status TEXT DEFAULT 'booked', manager_id TEXT, attendees TEXT,
    created_at TEXT, updated_at TEXT,
    UNIQUE (phone, project_id, visit_at)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT, kind TEXT, flow TEXT, run_at TEXT,
    status TEXT DEFAULT 'pending', payload TEXT, result TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (status, run_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT, phone TEXT, channel TEXT, kind TEXT, recipient TEXT,
    status TEXT, provider_id TEXT, error TEXT, payload TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS inbound (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT, provider_id TEXT UNIQUE, type TEXT, text TEXT, payload TEXT, received_at TEXT
);
CREATE TABLE IF NOT EXISTS webhook_events (
    call_id TEXT, event_type TEXT, payload TEXT, received_at TEXT,
    PRIMARY KEY (call_id, event_type)
);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now() -> str:
    return iso(clock.utcnow())


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


@contextmanager
def connect():
    path = get_settings().db_file
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


# ---------- webhook events ----------

def record_event(call_id: str, event_type: str, payload: dict) -> bool:
    """Returns False if this (call_id, event_type) was already processed."""
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO webhook_events VALUES (?, ?, ?, ?)",
            (call_id, event_type, json.dumps(payload), now()),
        )
        return cur.rowcount == 1


def completed_calls(limit: int = 100) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT payload FROM webhook_events WHERE event_type = 'all_processing_completed'"
            " ORDER BY received_at DESC LIMIT ?", (limit,),
        )
        return [json.loads(r["payload"]) for r in rows]


# ---------- leads ----------

def upsert_lead(phone: str, **fields) -> None:
    """Insert or update; None and "" never overwrite what we already know."""
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    ts = now()
    fields["updated_at"] = ts
    cols = ["phone", "created_at", *fields]
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    with connect() as c:
        c.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
            f" ON CONFLICT(phone) DO UPDATE SET {updates}",
            (phone, ts, *fields.values()),
        )


def set_if_empty(phone: str, column: str, value: str) -> None:
    assert column in {"first_call_at", "first_connect_at", "engaged_at"}
    with connect() as c:
        c.execute(f"UPDATE leads SET {column} = ? WHERE phone = ? AND {column} IS NULL", (value, phone))


def get_lead(phone: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None


def advance_stage(phone: str, stage: str) -> None:
    """Move forward in the funnel only. Side stages (nurture, lost, opted_out) are set directly."""
    lead = get_lead(phone)
    current = (lead or {}).get("stage") or "new"
    if stage in STAGES and current in STAGES and STAGES.index(stage) <= STAGES.index(current):
        return
    upsert_lead(phone, stage=stage)


def dormant_leads(before: str, limit: int, project_id: str | None = None) -> list[dict]:
    q = ("SELECT * FROM leads WHERE opted_out = 0 AND stage NOT IN ('booked', 'lost', 'visit_booked')"
         " AND COALESCE(last_contact_at, created_at) < ?")
    args: list = [before]
    if project_id:
        q += " AND project_id = ?"
        args.append(project_id)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY score DESC NULLS LAST LIMIT ?", (*args, limit))]


def leads_in_stage(stage: str, limit: int = 1000) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM leads WHERE stage = ? AND opted_out = 0 LIMIT ?", (stage, limit))]


def count_leads_by_manager() -> dict[str, int]:
    with connect() as c:
        rows = c.execute("SELECT manager_id, COUNT(*) n FROM leads WHERE manager_id IS NOT NULL GROUP BY manager_id")
        return {r["manager_id"]: r["n"] for r in rows}


# ---------- visits ----------

def add_visit(phone: str, name: str, project_id: str, visit_at: str, call_id: str, manager_id: str | None) -> int | None:
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO visits (phone, name, project_id, visit_at, call_id, manager_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (phone, name, project_id, visit_at, call_id, manager_id, now(), now()),
        )
        return cur.lastrowid if cur.rowcount == 1 else None


def get_visit(visit_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
        return dict(row) if row else None


def active_visit(phone: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM visits WHERE phone = ? AND status IN ('booked', 'confirmed') ORDER BY visit_at LIMIT 1",
            (phone,),
        ).fetchone()
        return dict(row) if row else None


def set_visit_status(visit_id: int, status: str) -> None:
    with connect() as c:
        c.execute("UPDATE visits SET status = ?, updated_at = ? WHERE id = ?", (status, now(), visit_id))


# ---------- tasks ----------

def add_task(phone: str, kind: str, flow: str, run_at: str, payload: dict, dedupe_key: str | None) -> int | None:
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO tasks (phone, kind, flow, run_at, payload, dedupe_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (phone, kind, flow, run_at, json.dumps(payload), dedupe_key, now(), now()),
        )
        return cur.lastrowid if cur.rowcount == 1 else None


def due_tasks(at: str, limit: int = 50) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE status = 'pending' AND run_at <= ? ORDER BY run_at LIMIT ?", (at, limit)
        )
        return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]


def finish_task(task_id: int, status: str, result: str | None = None, run_at: str | None = None) -> None:
    with connect() as c:
        if run_at:
            c.execute("UPDATE tasks SET run_at = ?, result = ?, updated_at = ? WHERE id = ?",
                      (run_at, result, now(), task_id))
        else:
            c.execute("UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                      (status, result, now(), task_id))


def cancel_tasks(phone: str, flow_prefix: str | None = None) -> int:
    q, args = "UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE phone = ? AND status = 'pending'", [now(), phone]
    if flow_prefix:
        q += " AND flow LIKE ?"
        args.append(flow_prefix + "%")
    with connect() as c:
        return c.execute(q, args).rowcount


def pending_tasks(phone: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM tasks WHERE phone = ? AND status = 'pending' ORDER BY run_at", (phone,))
        return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]


# ---------- messages ----------

def log_message(call_id, phone, channel, kind, recipient, status, provider_id=None, error=None, payload=None):
    with connect() as c:
        c.execute(
            "INSERT INTO messages (call_id, phone, channel, kind, recipient, status, provider_id, error, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, phone, channel, kind, recipient, status, provider_id, error,
             json.dumps(payload) if payload else None, now()),
        )


def was_sent(call_id: str, channel: str, kind: str) -> bool:
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM messages WHERE call_id = ? AND channel = ? AND kind = ? AND status IN ('sent', 'mocked')",
            (call_id, channel, kind),
        ).fetchone()
        return row is not None


def calls_since(phone: str, since: str) -> int:
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) FROM messages WHERE phone = ? AND channel = 'voice' AND status IN ('sent', 'mocked')"
            " AND created_at >= ?", (phone, since),
        ).fetchone()[0]


def record_inbound(phone: str, provider_id: str, type_: str, text: str, payload: dict) -> bool:
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inbound (phone, provider_id, type, text, payload, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            (phone, provider_id, type_, text, json.dumps(payload), now()),
        )
        return cur.rowcount == 1


def list_rows(table: str, limit: int = 50) -> list[dict]:
    assert table in {"leads", "visits", "tasks", "messages", "inbound", "webhook_events"}
    order = "updated_at" if table == "leads" else "rowid"
    with connect() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT ?", (limit,))]


def query(sql: str, args: tuple = ()) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]
