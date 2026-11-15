"""Real-estate presales agent: the integration service around Ringg.

/tools/*        Ringg API tools (pre-call and on-call)
/webhooks/*     Ringg call events, WhatsApp inbound
/leads, /visits, /campaigns   called by the client's systems and sales team
/admin/*        funnel dashboard and raw tables"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import cadence, db, scheduler  # noqa: F401  (cadence registers the task handlers)
from app.config import ROOT, get_settings
from app.routes import admin, leads, tools, webhooks

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    loop = asyncio.create_task(scheduler.loop()) if get_settings().scheduler_enabled else None
    yield
    if loop:
        loop.cancel()
        with suppress(asyncio.CancelledError):
            await loop


app = FastAPI(title="Real-estate presales agent", lifespan=lifespan)
(ROOT / "static" / "cost_sheets").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
for r in (tools.router, webhooks.router, leads.router, admin.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"ok": True, "mode": get_settings().delivery_mode}
