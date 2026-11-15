"""Cadence engine: a durable task queue in SQLite plus a polling loop.

A task is "do X for this lead at time T, if still relevant". Relevance is checked when the
task runs (guards), not when it is scheduled, because the lead may have answered, booked a
visit or opted out in between. Calls also respect the calling window and per-lead limits;
a call that can't go now is pushed to the next allowed time instead of being dropped."""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from app import clock, db
from app.config import get_settings

log = logging.getLogger("scheduler")
IST = ZoneInfo("Asia/Kolkata")

Handler = Callable[[dict, datetime], Awaitable[str]]
HANDLERS: dict[str, Handler] = {}


class Skip(Exception):
    """The task is no longer relevant (guard failed)."""


class Postpone(Exception):
    def __init__(self, until: datetime, reason: str):
        self.until, self.reason = until, reason


def handler(kind: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


def schedule(phone: str, kind: str, flow: str, run_at: datetime, payload: dict | None = None,
             dedupe: str | None = None) -> int | None:
    return db.add_task(phone, kind, flow, db.iso(run_at), payload or {}, dedupe)


# ---------- calling rules ----------

def next_call_slot(at: datetime) -> datetime:
    """`at` if it falls inside today's calling window (IST), else the next window opening."""
    s = get_settings()
    local = at.astimezone(IST)
    start, end = time(s.call_window_start), time(s.call_window_end)
    if local.time() < start:
        local = local.replace(hour=start.hour, minute=0, second=0, microsecond=0)
    elif local.time() >= end:
        local = (local + timedelta(days=1)).replace(hour=start.hour, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def call_limit_reason(phone: str, at: datetime) -> str | None:
    s = get_settings()
    day_start = at.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    if db.calls_since(phone, db.iso(day_start)) >= s.max_calls_per_day:
        return "daily call limit"
    if db.calls_since(phone, db.iso(at - timedelta(days=7))) >= s.max_calls_per_week:
        return "weekly call limit"
    return None


def check_guards(task: dict, now: datetime) -> None:
    """Common guards; each payload can also carry its own (see cadence.py)."""
    lead = db.get_lead(task["phone"]) or {}
    if lead.get("opted_out"):
        raise Skip("opted out")
    g = task["payload"].get("guard", {})
    if "not_after" in g and now > db.parse(g["not_after"]):
        raise Skip("too late to be useful")
    if "stage_in" in g and (lead.get("stage") or "new") not in g["stage_in"]:
        raise Skip(f"stage is {lead.get('stage')}")
    if "visit_status_in" in g:
        visit = db.get_visit(g["visit_id"])
        if not visit or visit["status"] not in g["visit_status_in"]:
            raise Skip(f"visit is {visit and visit['status']}")


# ---------- runner ----------

async def run_due(now: datetime | None = None) -> int:
    now = now or clock.utcnow()
    ran = 0
    for task in db.due_tasks(db.iso(now)):
        try:
            check_guards(task, now)
            fn = HANDLERS[task["kind"]]
            result = await fn(task, now)
            db.finish_task(task["id"], "done", result)
        except Skip as e:
            db.finish_task(task["id"], "cancelled", f"skipped: {e}")
        except Postpone as e:
            db.finish_task(task["id"], "pending", f"postponed: {e.reason}", run_at=db.iso(e.until))
        except Exception as e:  # keep the loop alive; the failure is visible in /admin/tasks
            log.exception("task %s failed", task["id"])
            db.finish_task(task["id"], "failed", repr(e)[:300])
        ran += 1
    return ran


async def loop() -> None:
    interval = get_settings().scheduler_interval_seconds
    while True:
        try:
            await run_due()
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
