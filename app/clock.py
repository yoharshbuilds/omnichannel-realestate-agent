"""Single source of "now", so tests (and replaying a day of traffic) can move time."""

from datetime import datetime, timezone

_frozen: datetime | None = None


def utcnow() -> datetime:
    return _frozen or datetime.now(timezone.utc)


def freeze(at: datetime | None) -> None:
    global _frozen
    _frozen = at
