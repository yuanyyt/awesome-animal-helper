"""Curated visitor facilities and sightseeing-shuttle facts for Hongshan Zoo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from src.backend.domain.models import (
    FacilityCategory,
    FacilityPoint,
    MapLocation,
    ShuttleSchedule,
    ShuttleService,
    ShuttleStation,
)


@dataclass(frozen=True)
class FacilitySeed:
    """Internal point with provenance used only while reconciling map data."""

    id: str
    name: str
    category: FacilityCategory
    longitude: float
    latitude: float
    address: str = "红山森林动物园内"
    source: str = "screenshot"

    def public(self) -> FacilityPoint:
        return FacilityPoint(
            id=self.id,
            name=self.name,
            category=self.category,
            longitude=self.longitude,
            latitude=self.latitude,
            address=self.address,
        )


class PoiLike(Protocol):
    name: str
    longitude: float
    latitude: float
    address: str
    typecode: str


# Coordinates are GCJ-02 points calibrated against the current AMap venue markers.
# Provenance intentionally never leaves this module.
FACILITY_SEEDS: tuple[FacilitySeed, ...] = (
    FacilitySeed("north-metro", "南京地铁红山动物园站", "metro", 118.79958, 32.09918, "和燕路"),
    FacilitySeed("south-metro", "南京地铁红山动物园站", "metro", 118.80102, 32.08886, "红山南路"),
    FacilitySeed("north-bus", "北门汽车客运站", "bus_terminal", 118.79888, 32.09858, "北门外"),
    FacilitySeed("south-bus", "南门汽车客运站", "bus_terminal", 118.80018, 32.08918, "南门外"),
    FacilitySeed("south-train", "红山动物园火车站", "train_station", 118.80115, 32.08865, "南门外"),
    FacilitySeed("north-visitor", "北门游客中心", "visitor_center", 118.799668, 32.093724),
    FacilitySeed("south-visitor", "南门游客中心", "visitor_center", 118.801349, 32.089895),
    FacilitySeed("north-gate", "北门", "entrance", 118.799844, 32.093933),
    FacilitySeed("east-gate", "东门", "entrance", 118.806880, 32.092110),
    FacilitySeed("south-gate", "南门", "entrance", 118.801349, 32.089895),
    FacilitySeed("north-storage", "北门寄存处", "bag_storage", 118.799720, 32.093760),
    FacilitySeed("south-storage", "南门寄存处", "bag_storage", 118.801420, 32.089880),
    FacilitySeed("north-ticket", "北门售票处", "ticket_office", 118.800059, 32.093928),
    FacilitySeed("south-ticket", "南门售票处", "ticket_office", 118.801300, 32.089820),
    FacilitySeed("north-parking", "北门停车场", "parking", 118.799420, 32.094080),
    FacilitySeed("south-parking", "南门停车场", "parking", 118.801520, 32.089740),
    FacilitySeed("north-smoking", "北门吸烟区", "smoking_area", 118.799760, 32.093650),
    FacilitySeed("central-smoking", "中心广场吸烟区", "smoking_area", 118.804250, 32.090410),
    FacilitySeed("north-water", "北门直饮水点", "drinking_water", 118.799850, 32.093620),
    FacilitySeed("panda-water", "熊猫馆直饮水点", "drinking_water", 118.801360, 32.092820),
    FacilitySeed("central-water", "中心广场直饮水点", "drinking_water", 118.804300, 32.090360),
    FacilitySeed("south-water", "南门直饮水点", "drinking_water", 118.801430, 32.089820),
    FacilitySeed("north-shuttle", "北门观光车站", "tour_bus_station", 118.799844, 32.093933),
    FacilitySeed("gorilla-shuttle", "猩猩馆观光车站", "tour_bus_station", 118.804486, 32.088680),
    FacilitySeed("central-shuttle", "中心广场观光车站", "tour_bus_station", 118.804355, 32.090318),
    FacilitySeed("east-shuttle", "东门观光车站", "tour_bus_station", 118.806880, 32.092110),
    FacilitySeed("monkey-shuttle", "猴山观光车站", "tour_bus_station", 118.803403, 32.091482),
    FacilitySeed("north-rental", "北门伴游车租赁", "mobility_rental", 118.799730, 32.093620),
    FacilitySeed("central-police", "园区警务室", "police", 118.804180, 32.090420),
    FacilitySeed("north-shop", "北门商店", "shopping", 118.799760, 32.093700),
    FacilitySeed("panda-shop", "熊猫馆商店", "shopping", 118.801490, 32.092820),
    FacilitySeed("central-shop", "中心广场商店", "shopping", 118.804270, 32.090300),
    FacilitySeed("south-shop", "南门商店", "shopping", 118.801420, 32.089840),
    FacilitySeed("north-restaurant", "北门餐厅", "restaurant", 118.799900, 32.093590),
    FacilitySeed("panda-restaurant", "熊猫馆餐厅", "restaurant", 118.801550, 32.092760),
    FacilitySeed("central-restaurant", "中心广场餐厅", "restaurant", 118.804143, 32.089780),
    FacilitySeed("south-restaurant", "南门餐厅", "restaurant", 118.801500, 32.089760),
    FacilitySeed("central-coffee", "中心广场咖啡", "coffee", 118.804073, 32.091780),
    FacilitySeed("north-toilet", "北门卫生间", "toilet", 118.799780, 32.093600),
    FacilitySeed("bird-toilet", "珍禽园卫生间", "toilet", 118.800537, 32.091858),
    FacilitySeed("panda-toilet", "熊猫馆卫生间", "toilet", 118.801216, 32.091868),
    FacilitySeed("primate-toilet", "亚洲灵长区卫生间", "toilet", 118.804562, 32.089254),
    FacilitySeed("east-toilet", "东门卫生间", "toilet", 118.805955, 32.091631),
    FacilitySeed("south-toilet", "南门卫生间", "toilet", 118.801390, 32.089790),
    FacilitySeed("north-nursing", "北门母婴室", "nursing_room", 118.799740, 32.093640),
    FacilitySeed("central-nursing", "中心广场母婴室", "nursing_room", 118.803225, 32.091875),
    FacilitySeed("north-family-toilet", "北门家庭卫生间", "family_toilet", 118.799800, 32.093580),
    FacilitySeed("panda-family-toilet", "熊猫馆家庭卫生间", "family_toilet", 118.801280, 32.091820),
    FacilitySeed("central-family-toilet", "中心广场家庭卫生间", "family_toilet", 118.803300, 32.091830),
)


_CATEGORY_KEYWORDS: tuple[tuple[FacilityCategory, tuple[str, ...]], ...] = (
    ("family_toilet", ("家庭卫生间", "第三卫生间")),
    ("nursing_room", ("母婴", "哺乳")),
    ("toilet", ("卫生间", "洗手间", "厕所")),
    ("drinking_water", ("直饮水", "饮水")),
    ("tour_bus_station", ("观光车", "游览车")),
    ("visitor_center", ("游客中心", "服务中心")),
    ("ticket_office", ("售票", "票务")),
    ("bag_storage", ("寄存", "行李")),
    ("smoking_area", ("吸烟",)),
    ("mobility_rental", ("伴游车", "轮椅", "童车租赁")),
    ("police", ("警务", "派出所")),
    ("coffee", ("咖啡",)),
    ("restaurant", ("餐厅", "餐饮", "小吃", "美食", "饭店")),
    ("shopping", ("商店", "便利店", "文创")),
    ("parking", ("停车场",)),
    ("entrance", ("入口", "出口", "北门", "南门", "东门")),
    ("metro", ("地铁",)),
    ("train_station", ("火车站", "铁路")),
    ("bus_terminal", ("客运站", "公交站")),
)


def public_facilities(pois: Iterable[PoiLike]) -> list[FacilityPoint]:
    """Prefer matching AMap points, then expose all curated points without provenance."""

    merged = list(FACILITY_SEEDS)
    for poi in pois:
        category = facility_category(poi.name, poi.typecode)
        if category is None:
            continue
        replacement = FacilitySeed(
            id=f"amap-{len(merged) + 1}",
            name=poi.name,
            category=category,
            longitude=poi.longitude,
            latitude=poi.latitude,
            address=poi.address or "红山森林动物园内",
            source="amap",
        )
        duplicate = next(
            (
                index
                for index, seed in enumerate(merged)
                if seed.category == category
                and (_normalized(seed.name) == _normalized(poi.name) or _near(seed, replacement))
            ),
            None,
        )
        if duplicate is None:
            merged.append(replacement)
        else:
            stable_id = merged[duplicate].id
            merged[duplicate] = FacilitySeed(**{**replacement.__dict__, "id": stable_id})
    return [seed.public() for seed in merged]


def facility_category(name: str, typecode: str = "") -> FacilityCategory | None:
    normalized = _normalized(name)
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(_normalized(keyword) in normalized for keyword in keywords):
            return category
    if typecode.startswith("050"):
        return "restaurant"
    if typecode.startswith("060"):
        return "shopping"
    return None


def shuttle_service() -> ShuttleService:
    station_ids = ("north-shuttle", "gorilla-shuttle", "central-shuttle", "east-shuttle", "monkey-shuttle")
    seeds = {seed.id: seed for seed in FACILITY_SEEDS}
    stations = [
        ShuttleStation(
            id=identifier,
            name=seeds[identifier].name.removesuffix("观光车站") + "站",
            order=index,
            longitude=seeds[identifier].longitude,
            latitude=seeds[identifier].latitude,
        )
        for index, identifier in enumerate(station_ids, start=1)
    ]
    route_points = [
        # 北门站 → 猩猩馆站
        MapLocation(longitude=118.799844, latitude=32.093933),
        MapLocation(longitude=118.800460, latitude=32.093329),
        MapLocation(longitude=118.800790, latitude=32.092326),
        MapLocation(longitude=118.801493, latitude=32.091102),
        MapLocation(longitude=118.802652, latitude=32.090412),
        MapLocation(longitude=118.803416, latitude=32.090161),
        MapLocation(longitude=118.804149, latitude=32.089900),
        MapLocation(longitude=118.804562, latitude=32.089132),
        MapLocation(longitude=118.804486, latitude=32.088680),
        # 猩猩馆站 → 中心广场站 → 东门站
        MapLocation(longitude=118.804293, latitude=32.089631),
        MapLocation(longitude=118.803902, latitude=32.090577),
        MapLocation(longitude=118.804355, latitude=32.090318),
        MapLocation(longitude=118.804952, latitude=32.090914),
        MapLocation(longitude=118.805664, latitude=32.091269),
        MapLocation(longitude=118.806200, latitude=32.091700),
        MapLocation(longitude=118.806880, latitude=32.092110),
        # 东门站 → 猴山站 → 北门站
        MapLocation(longitude=118.806148, latitude=32.092342),
        MapLocation(longitude=118.805388, latitude=32.092793),
        MapLocation(longitude=118.804632, latitude=32.092655),
        MapLocation(longitude=118.803900, latitude=32.092250),
        MapLocation(longitude=118.803403, latitude=32.091482),
        MapLocation(longitude=118.802882, latitude=32.091068),
        MapLocation(longitude=118.802218, latitude=32.091085),
        MapLocation(longitude=118.801497, latitude=32.091098),
        MapLocation(longitude=118.800955, latitude=32.091827),
        MapLocation(longitude=118.800794, latitude=32.092322),
        MapLocation(longitude=118.800547, latitude=32.093199),
        MapLocation(longitude=118.799844, latitude=32.093933),
    ]
    return ShuttleService(
        name="红山动物园观光游览车",
        stations=stations,
        polyline=route_points,
        schedules=[
            ShuttleSchedule(
                day_type="weekday",
                label="平日",
                fare_yuan=15,
                ticket_sales_start="08:30",
                ticket_sales_end="16:00",
                service_start="08:30",
                service_end="16:30",
            ),
            ShuttleSchedule(
                day_type="statutory_holiday",
                label="法定节假日",
                fare_yuan=20,
                ticket_sales_start="08:30",
                ticket_sales_end="16:30",
                service_start="08:30",
                service_end="17:00",
            ),
        ],
        notes=["身高1米以下儿童免票", "车票当日有效，隔日作废", "一经乘坐，不予退换"],
    )


def _near(first: FacilitySeed, second: FacilitySeed) -> bool:
    longitude_meters = (first.longitude - second.longitude) * 94_000
    latitude_meters = (first.latitude - second.latitude) * 111_000
    return longitude_meters**2 + latitude_meters**2 <= 25**2


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
