from __future__ import annotations

from datetime import date, timedelta
from typing import Optional


def next_editorial_slot(existing_dates: set[str], publish_days: tuple[str, ...], items_per_day: int) -> Optional[str]:
    if not publish_days or items_per_day < 1:
        return None
    day = date.today()
    names = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    for _ in range(366):
        iso = day.isoformat()
        if names[day.weekday()] in publish_days and sum(1 for item in existing_dates if item == iso) < items_per_day:
            return iso
        day += timedelta(days=1)
    return None
