"""India time for every timestamp the app writes. Servers (Streamlit Cloud, GitHub Actions) run in UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India has no daylight saving: a fixed offset is exact


def now_ist(source: Callable[[timezone], datetime] = datetime.now) -> datetime:
    """Current India time without an offset, so stored text keeps its "2026-11-14T16:05:00" format."""
    return source(IST).replace(tzinfo=None)
