"""Deterministic Shanghai time and statutory-holiday classification."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from src.backend.domain.models import ShuttleService

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 国务院办公厅国办发明电〔2025〕7号。
_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
}

NowProvider = Callable[[], datetime]


def zoo_operating_status(
    shuttle: ShuttleService,
    now_provider: NowProvider | None = None,
) -> dict[str, object]:
    """Return current local time and the matching shuttle schedule."""

    current = (now_provider or (lambda: datetime.now(SHANGHAI)))()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    else:
        current = current.astimezone(SHANGHAI)
    date_text = current.date().isoformat()
    calendar_supported = current.year == 2026
    is_holiday = calendar_supported and date_text in _HOLIDAYS_2026
    day_type = "statutory_holiday" if is_holiday else "weekday"
    schedule = next(item for item in shuttle.schedules if item.day_type == day_type)
    current_minutes = current.hour * 60 + current.minute
    start_minutes = _clock_minutes(schedule.service_start)
    end_minutes = _clock_minutes(schedule.service_end)
    operating = calendar_supported and start_minutes <= current_minutes <= end_minutes
    return {
        "timezone": "Asia/Shanghai",
        "current_time": current.isoformat(timespec="minutes"),
        "date": date_text,
        "weekday": current.strftime("%A"),
        "calendar_supported": calendar_supported,
        "day_type": day_type if calendar_supported else "unknown",
        "day_label": schedule.label if calendar_supported else "日历信息待更新",
        "is_statutory_holiday": is_holiday if calendar_supported else None,
        "shuttle_operating": operating,
        "fare_yuan": schedule.fare_yuan if calendar_supported else None,
        "ticket_sales": f"{schedule.ticket_sales_start}-{schedule.ticket_sales_end}",
        "service_hours": f"{schedule.service_start}-{schedule.service_end}",
    }


def _clock_minutes(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes
