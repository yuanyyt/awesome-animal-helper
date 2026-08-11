"""Curated zoo science-talk and animal-training schedules."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EducationSchedule:
    venue: str
    weekday_talks: tuple[str, ...] = ()
    holiday_talks: tuple[str, ...] = ()
    holiday_training: tuple[str, ...] = ()


EDUCATION_SCHEDULES: tuple[EducationSchedule, ...] = (
    EducationSchedule("大熊猫", ("10:30", "14:00", "15:00"), ("10:30", "14:00", "15:00")),
    EducationSchedule("细尾獭", ("14:20",), ("10:45",)),
    EducationSchedule("本土物种保育区", ("15:00",), ("15:00",)),
    EducationSchedule("狼", ("15:50",), ("11:00", "15:50")),
    EducationSchedule("虎", ("14:40",), ("11:00",), ("14:40-15:00",)),
    EducationSchedule("熊", ("15:00",), ("10:50", "15:00"), ("15:45-16:00",)),
    EducationSchedule("猫科星球", ("15:20",), ("10:30", "15:20")),
    EducationSchedule("中国猫科", ("15:30",), ("10:40", "15:30")),
    EducationSchedule("考拉", ("10:30", "14:40"), ("10:30", "14:40")),
    EducationSchedule("象", ("13:45",), ("13:45", "15:00"), ("10:00-10:20",)),
    EducationSchedule("高黎贡", ("10:45",), ("10:45", "15:00")),
    EducationSchedule("猩猩", ("14:40",), ("15:45",), ("14:40-15:00",)),
    EducationSchedule("亚洲灵长", ("15:00",), ("11:00",), ("15:00-15:30",)),
    EducationSchedule("犀鸟雨林", ("14:30",), ("10:30",), ("14:30-15:00",)),
    EducationSchedule("冈瓦纳栈道区", ("13:40",), ("13:40",)),
    EducationSchedule("冈瓦纳室内区", ("14:10",), ("14:10",)),
    EducationSchedule("冈瓦纳澳洲区", ("14:40",), ("14:40",)),
)

_VENUE_ALIASES = {
    "大熊猫馆": "大熊猫",
    "熊猫馆": "大熊猫",
    "考拉馆": "考拉",
    "猩猩馆": "猩猩",
    "亚洲灵长区": "亚洲灵长",
}


def education_schedule(venue: str = "") -> dict[str, object]:
    """Return all schedules or the entries matching one venue name."""

    query = _normalize(venue)
    canonical = _normalize(_VENUE_ALIASES.get(venue.strip(), venue))
    schedules = [
        item
        for item in EDUCATION_SCHEDULES
        if not query
        or canonical in _normalize(item.venue)
    ]
    return {
        "venue_query": venue.strip(),
        "schedules": [asdict(item) for item in schedules],
        "matched": len(schedules),
        "day_types": {
            "weekday_talks": "工作日科普讲解",
            "holiday_talks": "节假日科普讲解",
            "holiday_training": "节假日行为训练展示",
        },
        "notice": "因客流与场地限制，科普讲解及行为训练展示以现场实际工作为准。",
    }


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())
