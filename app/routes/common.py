import hmac

from fastapi import Header, HTTPException

from app.config import get_settings
from app.projects import all_projects


def secret_ok(given: str | None, expected: str) -> bool:
    token = (given or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(token, expected)


def require_tool_secret(x_tool_secret: str | None = Header(default=None)) -> None:
    if not secret_ok(x_tool_secret, get_settings().tool_secret):
        raise HTTPException(401, "bad tool secret")


def require_intake_secret(x_intake_secret: str | None = Header(default=None)) -> None:
    if not secret_ok(x_intake_secret, get_settings().intake_secret):
        raise HTTPException(401, "bad intake secret")


def reply(result: str, **extra) -> dict:
    """Tool responses: one sentence the agent can say or act on, plus structured fields."""
    return {"result": result, **extra}


def project_names() -> str:
    return ", ".join(p["name"] for p in all_projects())
