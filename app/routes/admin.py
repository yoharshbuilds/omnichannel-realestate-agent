"""Read-only views and the funnel dashboard. Protect /admin behind the client's SSO or a VPN in production."""

import statistics
from html import escape
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import db, scheduler

router = APIRouter(prefix="/admin")
FUNNEL = ["qualified", "visit_booked", "visit_done", "negotiation", "booked"]


def funnel() -> dict:
    leads = db.query("SELECT * FROM leads")
    n = len(leads)

    def reached(stage: str) -> int:
        later = FUNNEL[FUNNEL.index(stage):]
        visited = {v["phone"] for v in db.query("SELECT phone FROM visits")}
        done = {v["phone"] for v in db.query("SELECT phone FROM visits WHERE status = 'done'")}
        return sum(
            1 for lead in leads
            if lead["stage"] in later
            or (stage in ("qualified", "visit_booked") and lead["phone"] in visited)
            or (stage in ("qualified", "visit_booked", "visit_done") and lead["phone"] in done)
        )

    speeds = [
        (db.parse(lead["first_call_at"]) - db.parse(lead["created_at"])).total_seconds()
        for lead in leads if lead["first_call_at"] and lead["created_at"]
    ]
    visits = db.query("SELECT status, COUNT(*) n FROM visits GROUP BY status")
    vcount = {v["status"]: v["n"] for v in visits}
    attended = vcount.get("done", 0)
    resolved = attended + vcount.get("no_show", 0)
    called = sum(1 for lead in leads if lead["first_call_at"])
    return {
        "leads": n,
        "called": called,
        "connected": sum(1 for lead in leads if lead["first_connect_at"]),
        **{stage: reached(stage) for stage in FUNNEL},
        "median_seconds_to_first_call": round(statistics.median(speeds)) if speeds else None,
        "connect_rate": round(100 * sum(1 for lead in leads if lead["first_connect_at"]) / called) if called else None,
        "show_up_rate": round(100 * attended / resolved) if resolved else None,
        "opted_out": sum(1 for lead in leads if lead["opted_out"]),
        "by_source": db.query("SELECT COALESCE(source, 'unknown') source, COUNT(*) n FROM leads GROUP BY 1 ORDER BY 2 DESC"),
        "by_stage": db.query("SELECT COALESCE(stage, 'new') stage, COUNT(*) n FROM leads GROUP BY 1 ORDER BY 2 DESC"),
        "hot": db.query("SELECT phone, name, project_id, score, stage, manager_id FROM leads"
                        " WHERE opted_out = 0 ORDER BY score DESC NULLS LAST LIMIT 10"),
        "pending_tasks": db.query("SELECT COUNT(*) n FROM tasks WHERE status = 'pending'")[0]["n"],
    }


@router.get("/funnel")
def funnel_json():
    return funnel()


@router.post("/tick")
async def tick():
    """Run due tasks now (demos; production relies on the background loop)."""
    return {"ran": await scheduler.run_due()}


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    f = funnel()
    steps = [("Leads", f["leads"]), ("Called", f["called"]), ("Connected", f["connected"]),
             ("Qualified", f["qualified"]), ("Visit booked", f["visit_booked"]), ("Visit done", f["visit_done"]),
             ("Negotiation", f["negotiation"]), ("Booked", f["booked"])]
    top = max(f["leads"], 1)
    bars = "".join(
        f"<div class=row><span class=lbl>{label}</span><span class=bar style='width:{max(2, 100 * v / top):.0f}%'></span>"
        f"<span class=num>{v}</span></div>" for label, v in steps
    )

    def kpi(label, value, unit=""):
        return f"<div class=kpi><div class=k>{label}</div><div class=v>{'—' if value is None else f'{value}{unit}'}</div></div>"

    def table(rows, cols):
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{escape(str(r.get(c) if r.get(c) is not None else ''))}</td>" for c in cols)
                       + "</tr>" for r in rows)
        return f"<table><tr>{head}</tr>{body}</table>"

    html = f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Presales funnel</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--bar:#2f6fde}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121212;--fg:#eee;--muted:#999;--line:#333;--bar:#6c9cf0}}}}
body{{font:14px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px 16px;max-width:960px;margin:auto}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:28px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.kpi{{border:1px solid var(--line);border-radius:8px;padding:12px}} .k{{color:var(--muted);font-size:12px}} .v{{font-size:22px;font-weight:600}}
.row{{display:grid;grid-template-columns:110px 1fr 48px;align-items:center;gap:8px;margin:6px 0}}
.bar{{display:block;height:14px;background:var(--bar);border-radius:3px}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
table{{border-collapse:collapse;width:100%;overflow-x:auto;display:block}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-weight:500}}
</style></head><body>
<h1>Presales funnel</h1>
<div class=kpis>{kpi("Median time to first call", f["median_seconds_to_first_call"], " s")}{kpi("Connect rate", f["connect_rate"], "%")}
{kpi("Site-visit show-up", f["show_up_rate"], "%")}{kpi("Opted out", f["opted_out"])}{kpi("Queued touches", f["pending_tasks"])}</div>
<h2>Funnel</h2>{bars}
<h2>Top leads</h2>{table(f["hot"], ["name", "phone", "project_id", "score", "stage", "manager_id"])}
<h2>By source</h2>{table(f["by_source"], ["source", "n"])}
<h2>By stage</h2>{table(f["by_stage"], ["stage", "n"])}
</body></html>"""
    return HTMLResponse(html)


@router.get("/{table}")
def rows(table: Literal["leads", "visits", "tasks", "messages", "inbound", "webhook_events"], limit: int = 50):
    return db.list_rows(table, limit)
