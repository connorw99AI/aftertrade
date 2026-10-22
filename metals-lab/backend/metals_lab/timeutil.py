"""Single source of 'now' (UTC). Overridable for the simulated terminal and tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

_provider: Optional[Callable[[], datetime]] = None


def utc_now() -> datetime:
    return _provider() if _provider else datetime.now(timezone.utc)


def set_provider(fn: Optional[Callable[[], datetime]]) -> None:
    global _provider
    _provider = fn
