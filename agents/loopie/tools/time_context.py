"""Ground LLM scheduling in real wall-clock time (models do not know 'today' otherwise)."""

from __future__ import annotations

import os
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo


def now_line_for_llm() -> str:
    """Single line the model can use to resolve 'next Wednesday', 'tomorrow', etc."""
    tz_name = (os.environ.get("USER_TIMEZONE") or "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    now = datetime.now(tz)
    weekday = now.strftime("%A")
    # Helpful anchor for "next <weekday>" disambiguation
    days_until = []
    for i in range(1, 8):
        d = now.date() + timedelta(days=i)
        days_until.append(f"{d.isoformat()}={d.strftime('%A')}")
    week_ahead = ", ".join(days_until)
    return (
        f"REFERENCE_TIME: {now.isoformat()} (timezone={tz_name}), today_weekday={weekday}. "
        f"Next 7 dates: {week_ahead}. "
        "Use these facts to turn relative dates into explicit ISO-8601 start_iso and end_iso. "
        "If the user says 'next Wednesday', pick the Wednesday after today unless they clearly mean the same week."
    )
