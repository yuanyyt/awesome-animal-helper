<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { buildAmapNavigationTarget, isAndroidBrowser } from "../amapNavigation";
import { resolveAnimalImage } from "../animalImages";
import { isPointInPolygon, polygonBounds } from "../mapGeometry";
import type {
  AnimalDetail,
  FacilityCategory,
  FacilityPoint,
  MapGuide,
  MapLocationState,
  MapNamedLocation,
  MapOriginSource,
  MapPoint,
  RouteOption,
} from "../types";
import AnimalPhoto from "./AnimalPhoto.vue";

const props = defineProps<{
  guide?: MapGuide;
  animals: AnimalDetail[];
  selectedAnimals: AnimalDetail[];
  selectedSite: string;
  routeSites: string[];
  origin: MapNamedLocation | null;
  originSource: MapOriginSource;
  originPickRequest: number;
  activeRoute: RouteOption | null;
  active: boolean;
  focused: boolean;
  loading: boolean;
  error: string;
}>();

type MapPointLike = { longitude: number; latitude: number };

// AMap requires literal hexadecimal overlay colors instead of CSS variables.
const AMAP_COLORS = {
  paper: "#fffdf7",
  accent: "#285c48",
  walking: "#c65f42",
  shuttle: "#167d8d",
  activeShuttle: "#0c6876",
} as const;

const emit = defineEmits<{
  select: [site: string];
  routeToggle: [site: string];
  originChange: [origin: MapNamedLocation, source: MapOriginSource];
  locationStateChange: [state: MapLocationState];
  retry: [];
}>();

const imageFailed = ref(false);
const interactiveFailed = ref(false);
const interactiveReady = ref(false);
const settingOrigin = ref(false);
const locationState = ref<MapLocationState>("idle");
watch(locationState, (state) => emit("locationStateChange", state), { immediate: true });
const mapContainer = ref<HTMLElement>();
let map: AmapMap | undefined;
let amapMarkers: AmapMarker[] = [];
let facilityMarkers: { marker: AmapMarker; facility: FacilityPoint }[] = [];
let routeOverlays: AmapOverlay[] = [];
let shuttleOverlay: AmapOverlay | undefined;
let originMarker: AmapMarker | undefined;
let boundaryLine: AmapOverlay | undefined;
let mapBounds: AmapBounds | undefined;
let mapLimitBounds: AmapBounds | undefined;
let readinessTimer: number | undefined;
let mapResizeObserver: ResizeObserver | undefined;
let mapResizeFrame: number | undefined;
let mapResizeSettleTimer: number | undefined;
let observedMapSize = "";
let locationAttempt = 0;
let automaticLocationAttempted = false;
let amapFallbackTimer: number | undefined;
let amapVisibilityHandler: (() => void) | undefined;
const selectedPoint = computed(() =>
  props.guide?.points.find((point) => point.site === props.selectedSite),
);
const selectedFacility = ref<FacilityPoint | null>(null);
type FacilityGroup = "common" | "refreshment" | "shopping" | "family" | "transport" | "none";
type FacilityVisualGroup = Exclude<FacilityGroup, "none">;
type MapPanel = "place" | "services" | "route" | "origin" | null;
const activeFacilityGroup = ref<FacilityGroup>("none");
const activePanel = ref<MapPanel>(null);
const pendingOrigin = ref<MapNamedLocation | null>(null);
const facilityGroups: {
  id: FacilityGroup;
  label: string;
  categories: FacilityCategory[];
}[] = [
  {
    id: "common",
    label: "游客服务",
    categories: ["entrance", "visitor_center", "ticket_office", "bag_storage"],
  },
  {
    id: "refreshment",
    label: "休息补给",
    categories: ["drinking_water", "restaurant", "coffee"],
  },
  {
    id: "shopping",
    label: "文创购物",
    categories: ["shopping"],
  },
  {
    id: "family",
    label: "卫生亲子",
    categories: ["toilet", "family_toilet", "nursing_room"],
  },
  {
    id: "transport",
    label: "出行保障",
    categories: [
      "metro", "bus_terminal", "train_station", "parking", "tour_bus_station",
      "mobility_rental", "police", "smoking_area",
    ],
  },
];
const activeFacilityCategories = computed(
  () => new Set(
    facilityGroups.find((group) => group.id === activeFacilityGroup.value)?.categories ?? [],
  ),
);
const visibleFacilities = computed(() =>
  (props.guide?.facilities ?? []).filter((facility) =>
    activeFacilityCategories.value.has(facility.category) || isFacilityRouteStop(facility),
  ),
);
const displayedRouteSites = computed(() => props.activeRoute?.sites ?? props.routeSites);
const selectedPointInRoute = computed(() =>
  selectedPoint.value ? displayedRouteSites.value.includes(selectedPoint.value.site) : false,
);
const durationLabel = computed(() =>
  props.activeRoute ? `约 ${props.activeRoute.total_minutes} 分钟` : "",
);
const amapNavigationTarget = computed(() =>
  props.activeRoute ? buildAmapNavigationTarget(props.activeRoute) : null,
);
const locationStatus = computed(() => {
  if (settingOrigin.value) return "请在园区地图内点击新的起点";
  const labels: Record<MapLocationState, string> = {
    idle: props.origin ? `路线起点：${props.origin.name}` : "等待确认路线起点",
    locating: "正在确认你在园里的位置…",
    inside: props.origin?.name === "当前位置"
      ? "已定位到园内，将从当前位置出发"
      : `路线起点：${props.origin?.name ?? "园区入口"}`,
    outside: "你在园外，将从南门新区出发",
    failed: "未取得定位，将从南门新区出发",
    manual: "已使用地图选定起点",
  };
  return labels[locationState.value];
});
const routeLegs = computed(() => {
  const route = props.activeRoute;
  if (!route) return [];
  const legs = route.legs
    .map((leg, index) => ({
      ...leg,
      id: `${index}-${leg.from_name}-${leg.to_name}`,
      minutes: Math.max(1, Math.ceil(leg.duration_seconds / 60)),
      path: leg.mode === "shuttle" ? pathInsideBoundary(leg.polyline) : leg.polyline,
    }))
    .filter((leg) => leg.path.length >= 2);
  if (legs.length || route.polyline.length < 2) return legs;
  return [{
    id: `fallback-${route.id}`,
    from_name: props.origin?.name ?? "园区入口",
    to_name: route.sites.at(-1) ?? "路线终点",
    distance_meters: route.distance_meters,
    duration_seconds: route.walking_minutes * 60,
    steps: [],
    polyline: route.polyline,
    minutes: route.walking_minutes,
    path: route.polyline,
    mode: "walking" as const,
    estimated: false,
  }];
});
const routeAnimalsBySite = computed(() => {
  const animalsBySite = new Map<string, AnimalDetail[]>();
  const routeSites = displayedRouteSites.value;
  const candidates = props.selectedAnimals.length ? props.selectedAnimals : props.animals;

  for (const animal of candidates) {
    const site = routeSites.find((candidate) => animal.sites.includes(candidate));
    if (!site) continue;
    const animals = animalsBySite.get(site) ?? [];
    if (animals.length < 2) animals.push(animal);
    animalsBySite.set(site, animals);
  }
  return animalsBySite;
});
const boundaryPath = computed(() => props.guide?.boundary.points ?? []);
const boundaryCoordinates = computed(() =>
  boundaryPath.value.map(
    (point) => [point.longitude, point.latitude] as [number, number],
  ),
);
const staticBoundaryStyle = computed<Record<string, string>>(() => {
  if (!props.guide || boundaryPath.value.length < 3) return {} as Record<string, string>;
  const center = project(
    props.guide.center.longitude,
    props.guide.center.latitude,
    props.guide.zoom,
  );
  const points = boundaryPath.value.map((point) => {
    const target = project(point.longitude, point.latitude, props.guide!.zoom);
    const left = 50 + ((target.x - center.x) / 1024) * 100;
    const top = 50 + ((target.y - center.y) / 640) * 100;
    return `${left.toFixed(2)}% ${top.toFixed(2)}%`;
  });
  return { "--zoo-boundary-clip": `polygon(${points.join(", ")})` };
});

watch(
  [() => props.guide, () => props.active],
  ([guide, active]) => {
    if (guide && active && !map && !interactiveFailed.value) {
      void initializeInteractiveMap();
    }
  },
  { immediate: true },
);

watch(
  () => props.originPickRequest,
  (request, previous) => {
    if (!request || request === previous) return;
    beginOriginSelection();
  },
);

watch(
  () => props.routeSites,
  () => updateInteractiveMarkers(props.selectedSite),
  { deep: true },
);

watch(
  () => props.activeRoute,
  () => {
    if (props.activeRoute) activeFacilityGroup.value = "none";
    updateInteractiveMarkers(props.selectedSite);
    updateFacilityVisibility();
    updateShuttleVisibility();
    updateRouteOverlay();
  },
  { deep: true },
);

watch(
  () => props.origin,
  () => updateOriginMarker(),
  { deep: true },
);

watch(
  () => props.originSource,
  (source) => {
    if (source !== "explicit") return;
    locationAttempt += 1;
    settingOrigin.value = false;
    pendingOrigin.value = null;
    locationState.value = "manual";
  },
);

watch(activePanel, (panel) => {
  if (panel !== "route" || !props.activeRoute) return;
  void nextTick(() => {
    window.requestAnimationFrame(() => fitRouteOverlays());
  });
});

watch(
  () => props.selectedSite,
  (site) => {
    const point = props.guide?.points.find((candidate) => candidate.site === site);
    if (site) selectedFacility.value = null;
    if (site) activePanel.value = "place";
    updateInteractiveMarkers(site);
    if (point && map) map.panTo([point.longitude, point.latitude]);
  },
);

watch(
  () => props.focused,
  () => scheduleMapViewportSync(),
);

onBeforeUnmount(() => {
  clearAmapFallback();
  destroyInteractiveMap();
});

async function initializeInteractiveMap(): Promise<void> {
  destroyInteractiveMap();
  const config = props.guide?.js_api;
  if (!config) return;
  interactiveFailed.value = false;
  interactiveReady.value = false;
  await nextTick();
  try {
    const AMap = await loadAmap(config.api_key, config.service_host);
    if (!mapContainer.value || !props.guide) return;
    const path = boundaryCoordinates.value;
    map = new AMap.Map(mapContainer.value, {
      center: [props.guide.center.longitude, props.guide.center.latitude],
      zoom: props.guide.zoom,
      viewMode: "2D",
      resizeEnable: true,
      dragEnable: true,
      zoomEnable: true,
      touchZoom: true,
      scrollWheel: true,
      doubleClickZoom: true,
      keyboardEnable: true,
    });
    observeMapContainer();
    const bounds = createAmapBounds(AMap, path);
    mapBounds = bounds;
    mapLimitBounds = createAmapBounds(AMap, path, 0.45);
    if (path.length >= 3) {
      boundaryLine = new AMap.Polygon({
        path: outsideMaskPath(path),
        fillColor: AMAP_COLORS.paper,
        fillOpacity: 0.78,
        strokeColor: AMAP_COLORS.accent,
        strokeOpacity: 0.72,
        strokeWeight: 3,
        zIndex: 20,
      });
      map.add(boundaryLine);
    }
    map.on("complete", markInteractiveMapReady);
    map.on("click", handleMapClick);
    map.on("zoomend", updateMarkerZoomState);
    readinessTimer = window.setTimeout(() => {
      if (interactiveReady.value) return;
      destroyInteractiveMap();
      interactiveFailed.value = true;
    }, 10_000);
    amapMarkers = props.guide.points.map((point, index) => {
      const content = createMarkerButton(point, index);
      const marker = new AMap.Marker({
        position: [point.longitude, point.latitude],
        content,
        offset: new AMap.Pixel(-16, -16),
        title: point.poi_name,
        zIndex: 220,
      });
      content.addEventListener("click", (event) => {
        event.stopPropagation();
        selectVenue(point.site);
      });
      return marker;
    });
    map.add(amapMarkers);
    createFacilityMarkers();
    createShuttleOverlay();
    updateInteractiveMarkers(props.selectedSite);
    updateOriginMarker();
    updateRouteOverlay();
    if (!automaticLocationAttempted) {
      automaticLocationAttempted = true;
      void locateVisitor();
    }
  } catch {
    destroyInteractiveMap();
    interactiveFailed.value = true;
    locationState.value = "failed";
  }
}

function markInteractiveMapReady(): void {
  if (readinessTimer !== undefined) window.clearTimeout(readinessTimer);
  readinessTimer = undefined;
  interactiveReady.value = true;
  if (map && mapLimitBounds) map.setLimitBounds(mapLimitBounds);
  updateRouteOverlay();
  scheduleMapViewportSync();
}

function observeMapContainer(): void {
  const container = mapContainer.value;
  if (!container || typeof ResizeObserver === "undefined") return;
  mapResizeObserver?.disconnect();
  observedMapSize = "";
  mapResizeObserver = new ResizeObserver(([entry]) => {
    if (!entry) return;
    const size = `${Math.round(entry.contentRect.width)}x${Math.round(entry.contentRect.height)}`;
    if (size === observedMapSize) return;
    observedMapSize = size;
    if (mapResizeFrame !== undefined) window.cancelAnimationFrame(mapResizeFrame);
    mapResizeFrame = window.requestAnimationFrame(() => {
      mapResizeFrame = undefined;
      map?.resize();
      fitCurrentViewport();
    });
  });
  mapResizeObserver.observe(container);
}

function scheduleMapViewportSync(): void {
  if (mapResizeFrame !== undefined) window.cancelAnimationFrame(mapResizeFrame);
  if (mapResizeSettleTimer !== undefined) window.clearTimeout(mapResizeSettleTimer);
  void nextTick(() => {
    mapResizeFrame = window.requestAnimationFrame(() => {
      mapResizeFrame = undefined;
      map?.resize();
      fitCurrentViewport();
      // Teleport and mobile browser chrome can settle after the first layout frame.
      mapResizeSettleTimer = window.setTimeout(() => {
        mapResizeSettleTimer = undefined;
        map?.resize();
        fitCurrentViewport();
      }, 160);
    });
  });
}

function fitCurrentViewport(): void {
  if (props.activeRoute) {
    fitRouteOverlays();
    return;
  }
  if (!map || !mapBounds) return;
  const compact = (mapContainer.value?.clientHeight ?? 0) <= 260;
  const edgePadding = compact ? 12 : 32;
  map.setBounds(mapBounds, true, [edgePadding, edgePadding, edgePadding, edgePadding]);
}

function createMarkerButton(point: MapPoint, index: number): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "zoo-map__amap-marker";
  button.classList.toggle("is-label-left", index % 2 === 1);
  button.dataset.site = point.site;
  button.dataset.defaultIndex = String(index + 1);
  button.setAttribute("aria-label", `查看${point.site}，${point.animal_count}种动物`);
  button.setAttribute("aria-pressed", "false");
  return button;
}

function selectVenue(site: string): void {
  if (settingOrigin.value) {
    const point = props.guide?.points.find((candidate) => candidate.site === site);
    if (point) setPendingOrigin(point.longitude, point.latitude);
    return;
  }
  selectedFacility.value = null;
  emit("select", site);
  activePanel.value = "place";
}

function createFacilityMarkers(): void {
  if (!map || !window.AMap || !props.guide) return;
  const AMap = window.AMap;
  facilityMarkers = props.guide.facilities.map((facility) => {
    const content = document.createElement("button");
    content.type = "button";
    content.className = "zoo-map__facility-marker";
    content.dataset.group = facilityVisualGroup(facility.category);
    content.append(createFacilityIcon(facility.category));
    content.title = facility.name;
    content.setAttribute("aria-label", `${facility.name}，${facilityLabel(facility.category)}`);
    content.addEventListener("click", (event) => {
      event.stopPropagation();
      selectFacility(facility);
    });
    const marker = new AMap.Marker({
      position: [facility.longitude, facility.latitude],
      content,
      offset: new AMap.Pixel(-14, -14),
      title: facility.name,
      zIndex: 190,
    });
    return { marker, facility };
  });
  map.add(facilityMarkers.map((item) => item.marker));
  updateFacilityVisibility();
}

function createShuttleOverlay(): void {
  if (!map || !window.AMap || !props.guide?.shuttle) return;
  const AMap = window.AMap;
  shuttleOverlay = new AMap.Polyline({
    path: pathInsideBoundary(props.guide.shuttle.polyline).map(
      (point) => [point.longitude, point.latitude] as [number, number],
    ),
    strokeColor: AMAP_COLORS.shuttle,
    strokeWeight: 4,
    strokeOpacity: 0.62,
    strokeStyle: "dashed",
    lineJoin: "round",
    lineCap: "round",
    showDir: false,
    zIndex: 120,
  });
  map.add(shuttleOverlay);
  updateShuttleVisibility();
}

function updateShuttleVisibility(): void {
  if (!shuttleOverlay) return;
  const visible = activeFacilityGroup.value === "transport" || props.activeRoute?.uses_shuttle;
  if (visible) shuttleOverlay.show?.();
  else shuttleOverlay.hide?.();
}

function pathInsideBoundary(points: MapPointLike[]): MapPointLike[] {
  if (!props.guide || boundaryPath.value.length < 3 || points.length < 2) return points;
  const result: MapPointLike[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    for (let step = 0; step < 8; step += 1) {
      const ratio = step / 8;
      const point = {
        longitude: start.longitude + (end.longitude - start.longitude) * ratio,
        latitude: start.latitude + (end.latitude - start.latitude) * ratio,
      };
      result.push(moveInsideBoundary(point));
    }
  }
  result.push(moveInsideBoundary(points.at(-1)!));
  return result;
}

function moveInsideBoundary(point: MapPointLike): MapPointLike {
  if (!props.guide || isPointInPolygon(point, boundaryPath.value)) return point;
  const center = props.guide.center;
  for (let step = 1; step <= 20; step += 1) {
    const ratio = step / 20;
    const candidate = {
      longitude: point.longitude + (center.longitude - point.longitude) * ratio,
      latitude: point.latitude + (center.latitude - point.latitude) * ratio,
    };
    if (isPointInPolygon(candidate, boundaryPath.value)) return candidate;
  }
  return center;
}

function selectFacilityGroup(group: FacilityGroup): void {
  activeFacilityGroup.value = group;
  selectedFacility.value = null;
  updateFacilityVisibility();
  updateShuttleVisibility();
}

function selectFacility(facility: FacilityPoint): void {
  if (settingOrigin.value) {
    setPendingOrigin(facility.longitude, facility.latitude);
    return;
  }
  selectedFacility.value = facility;
  activePanel.value = "place";
}

function toggleServicesPanel(): void {
  activePanel.value = activePanel.value === "services" ? null : "services";
}

function toggleRoutePanel(): void {
  activePanel.value = activePanel.value === "route" ? null : "route";
}

function openInAmap(): void {
  const target = amapNavigationTarget.value;
  if (!target) return;
  if (!isAndroidBrowser()) {
    window.open(target.h5Uri, "_blank", "noopener,noreferrer");
    return;
  }

  clearAmapFallback();
  amapVisibilityHandler = () => {
    if (document.visibilityState === "hidden") clearAmapFallback();
  };
  document.addEventListener("visibilitychange", amapVisibilityHandler);
  window.location.href = target.androidUri;
  amapFallbackTimer = window.setTimeout(() => {
    clearAmapFallback();
    if (document.visibilityState === "visible") window.location.href = target.fallbackH5Uri;
  }, 2_000);
}

function clearAmapFallback(): void {
  if (amapFallbackTimer !== undefined) window.clearTimeout(amapFallbackTimer);
  amapFallbackTimer = undefined;
  if (amapVisibilityHandler) {
    document.removeEventListener("visibilitychange", amapVisibilityHandler);
    amapVisibilityHandler = undefined;
  }
}

function closePanel(): void {
  activePanel.value = null;
  selectedFacility.value = null;
}

function toggleSelectedPointRoute(): void {
  if (selectedPoint.value) emit("routeToggle", selectedPoint.value.site);
}

function updateFacilityVisibility(): void {
  for (const { marker, facility } of facilityMarkers) {
    const content = marker.getContent();
    const routeStop = isFacilityRouteStop(facility);
    content.classList.toggle("is-route-stop", routeStop);
    content.hidden = !activeFacilityCategories.value.has(facility.category) && !routeStop;
  }
}

function isFacilityRouteStop(facility: FacilityPoint): boolean {
  const routeNames = new Set(displayedRouteSites.value);
  return routeNames.has(facility.name)
    || facility.aliases.some((name) => routeNames.has(name));
}

function facilityIconPath(category: FacilityCategory): string {
  const icons: Record<FacilityCategory, string> = {
    metro: "M5 18h14M7 15l1.5-9h7L17 15M9 10h6M8 15h8",
    bus_terminal: "M6 17h12V7c0-2-2-3-6-3S6 5 6 7v10Zm0-5h12M8 20h.01M16 20h.01",
    train_station: "M7 16h10V6c0-2-2-3-5-3S7 4 7 6v10Zm0-5h10M9 20l3-4 3 4",
    visitor_center: "M12 10v7M12 7h.01M4 12a8 8 0 1 0 16 0 8 8 0 0 0-16 0Z",
    entrance: "M5 21V4h11v17M9 12h.01M16 8h3v13",
    bag_storage: "M5 8h14l-1 12H6L5 8Zm4 0V6c0-2 6-2 6 0v2",
    ticket_office: "M4 8h16v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4V8Zm8 2v6",
    parking: "M7 20V4h6a5 5 0 0 1 0 10H7M7 14h6",
    smoking_area: "M4 15h13M5 18h12M18 15h2v3h-2M9 11c0-2 4-1 3-5M14 11c0-2 4-1 3-5",
    drinking_water: "M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11Z",
    tour_bus_station: "M5 16h14V8c0-2-2-3-7-3S5 6 5 8v8Zm0-5h14M8 19h.01M16 19h.01",
    mobility_rental: "M5 17a3 3 0 1 0 0 .01M19 17a3 3 0 1 0 0 .01M8 17l3-7h4l4 7M9 13h7M12 10l-2-3",
    police: "M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6l-7-3Zm0 5v8M8 12h8",
    shopping: "M5 8h14l-1 12H6L5 8Zm4 0a3 3 0 0 1 6 0",
    restaurant: "M7 3v8M4 3v5c0 2 6 2 6 0V3M7 11v10M16 3v18M16 3c4 2 4 8 0 10",
    coffee: "M5 8h12v7a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5V8Zm12 2h2a3 3 0 0 1 0 6h-2M8 4v2M12 4v2",
    toilet: "M8 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm8 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM5 21v-7H3l2-6h6l2 6h-2v7M15 21v-5h-2l2-8h2l2 8h-2v5",
    nursing_room: "M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm-3 14v-6H7l2-6h5l3 4M13 16a4 4 0 1 0 8 0 4 4 0 0 0-8 0Z",
    family_toilet: "M7 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0 15v-6H5l2-7h2l2 7H9v6M16 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm0 11v-4h-2l2-5 2 5h-2",
  };
  return icons[category];
}

function createFacilityIcon(category: FacilityCategory): SVGSVGElement {
  const namespace = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(namespace, "svg");
  const path = document.createElementNS(namespace, "path");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  path.setAttribute("d", facilityIconPath(category));
  icon.append(path);
  return icon;
}

function facilityLabel(category: FacilityCategory): string {
  const labels: Record<FacilityCategory, string> = {
    metro: "地铁", bus_terminal: "汽车客运", train_station: "火车站",
    visitor_center: "游客中心", entrance: "出入口", bag_storage: "寄存",
    ticket_office: "售票处", parking: "停车场", smoking_area: "吸烟区",
    drinking_water: "直饮水", tour_bus_station: "观光车站",
    mobility_rental: "伴游车租赁", police: "警务室", shopping: "商店",
    restaurant: "餐饮", coffee: "咖啡", toilet: "卫生间",
    nursing_room: "母婴室", family_toilet: "家庭卫生间",
  };
  return labels[category];
}

function facilityVisualGroup(category: FacilityCategory): FacilityVisualGroup {
  if (["visitor_center", "entrance", "bag_storage", "ticket_office"].includes(category)) {
    return "common";
  }
  if (["drinking_water", "restaurant", "coffee"].includes(category)) {
    return "refreshment";
  }
  if (category === "shopping") return "shopping";
  if (["toilet", "family_toilet", "nursing_room"].includes(category)) return "family";
  return "transport";
}

function updateInteractiveMarkers(site: string): void {
  for (const marker of amapMarkers) {
    const button = marker.getContent();
    const markerSite = button.dataset.site ?? "";
    const routeIndex = displayedRouteSites.value.indexOf(markerSite);
    const active = markerSite === site;
    const routeSite = routeIndex >= 0;
    button.classList.toggle("is-active", active);
    button.classList.toggle("is-route-stop", routeSite);
    button.classList.remove("is-label-visible");
    button.classList.toggle(
      "is-label-left",
      (routeSite ? routeIndex : Number(button.dataset.defaultIndex ?? 1) - 1) % 2 === 1,
    );
    renderMarkerContent(
      button,
      routeSite ? routeIndex + 1 : 0,
      routeSite ? routeAnimals(markerSite) : [],
    );
    button.setAttribute("aria-pressed", String(active));
  }
}

function updateMarkerZoomState(): void {
  for (const marker of amapMarkers) {
    marker.getContent().classList.remove("is-label-visible");
  }
}

function routeAnimals(site: string): AnimalDetail[] {
  return routeAnimalsBySite.value.get(site) ?? [];
}

function renderMarkerContent(
  button: HTMLButtonElement,
  markerNumber: number,
  animals: AnimalDetail[],
): void {
  const renderToken = crypto.randomUUID();
  button.dataset.renderToken = renderToken;
  button.replaceChildren();

  const number = document.createElement("span");
  number.className = "zoo-map__marker-number";
  number.textContent = markerNumber > 0 ? String(markerNumber) : "";
  const siteName = document.createElement("span");
  siteName.className = "zoo-map__marker-site-name";
  siteName.textContent = button.dataset.site ?? "";
  button.append(number, siteName);
  if (!animals.length) return;

  const stack = document.createElement("span");
  stack.className = "zoo-map__animal-stack";
  stack.setAttribute("aria-hidden", "true");
  button.append(stack);
  for (const animal of animals) {
    const portrait = document.createElement("span");
    portrait.className = "zoo-map__animal-circle is-fallback";
    portrait.textContent = animal.name.slice(0, 1);
    portrait.title = animal.name;
    stack.append(portrait);
    void resolveAnimalImage(animal).then((url) => {
      if (!url || button.dataset.renderToken !== renderToken) return;
      const image = document.createElement("img");
      image.src = url;
      image.alt = "";
      image.addEventListener("load", () => portrait.classList.remove("is-fallback"));
      image.addEventListener("error", () => image.remove());
      portrait.replaceChildren(image);
    });
  }
}

function markerNumber(point: MapPoint): number {
  const routeIndex = displayedRouteSites.value.indexOf(point.site);
  return routeIndex >= 0 ? routeIndex + 1 : 0;
}

function markerLabelLeft(point: MapPoint, fallbackIndex: number): boolean {
  const routeIndex = displayedRouteSites.value.indexOf(point.site);
  return (routeIndex >= 0 ? routeIndex : fallbackIndex) % 2 === 1;
}

function destroyInteractiveMap(): void {
  locationAttempt += 1;
  if (readinessTimer !== undefined) window.clearTimeout(readinessTimer);
  readinessTimer = undefined;
  mapResizeObserver?.disconnect();
  mapResizeObserver = undefined;
  if (mapResizeFrame !== undefined) window.cancelAnimationFrame(mapResizeFrame);
  mapResizeFrame = undefined;
  if (mapResizeSettleTimer !== undefined) window.clearTimeout(mapResizeSettleTimer);
  mapResizeSettleTimer = undefined;
  observedMapSize = "";
  amapMarkers = [];
  facilityMarkers = [];
  routeOverlays = [];
  originMarker = undefined;
  boundaryLine = undefined;
  shuttleOverlay = undefined;
  mapBounds = undefined;
  mapLimitBounds = undefined;
  map?.destroy();
  map = undefined;
  interactiveReady.value = false;
}

function handleMapClick(event: AmapMapClickEvent): void {
  if (!settingOrigin.value) {
    closePanel();
    return;
  }
  setPendingOrigin(event.lnglat.getLng(), event.lnglat.getLat());
}

function setPendingOrigin(longitude: number, latitude: number): void {
  const selectedOrigin = { name: "地图选定起点", longitude, latitude };
  if (!isPointInPolygon(selectedOrigin, boundaryPath.value)) return;
  locationAttempt += 1;
  pendingOrigin.value = selectedOrigin;
  updateOriginMarker();
}

function toggleManualOrigin(): void {
  if (settingOrigin.value) cancelManualOrigin();
  else beginOriginSelection();
}

function beginOriginSelection(): void {
  locationAttempt += 1;
  if (locationState.value === "locating") locationState.value = "idle";
  pendingOrigin.value = null;
  settingOrigin.value = true;
  activePanel.value = "origin";
}

function confirmManualOrigin(): void {
  if (!pendingOrigin.value) return;
  const origin = pendingOrigin.value;
  locationState.value = "manual";
  pendingOrigin.value = null;
  settingOrigin.value = false;
  activePanel.value = null;
  emit("originChange", origin, "map");
}

function cancelManualOrigin(): void {
  pendingOrigin.value = null;
  settingOrigin.value = false;
  activePanel.value = null;
  updateOriginMarker();
}

async function locateVisitor(): Promise<void> {
  activePanel.value = null;
  const AMap = window.AMap;
  if (!AMap || !map || !props.guide) {
    useDefaultOrigin("failed");
    return;
  }

  const attempt = ++locationAttempt;
  settingOrigin.value = false;
  pendingOrigin.value = null;
  locationState.value = "locating";
  try {
    await loadAmapPlugin(AMap, "AMap.Geolocation");
    if (attempt !== locationAttempt) return;
    const geolocation = new AMap.Geolocation({
      enableHighAccuracy: true,
      timeout: 10_000,
      convert: true,
      showButton: false,
      showMarker: false,
      panToLocation: false,
      zoomToAccuracy: false,
    });
    geolocation.getCurrentPosition((status, result) => {
      if (attempt !== locationAttempt) return;
      if (status !== "complete" || !result.position) {
        useDefaultOrigin("failed", attempt);
        return;
      }
      const current = {
        longitude: result.position.getLng(),
        latitude: result.position.getLat(),
      };
      if (!isPointInPolygon(current, boundaryPath.value)) {
        useDefaultOrigin("outside", attempt);
        return;
      }
      locationState.value = "inside";
      emit("originChange", { name: "当前位置", ...current }, "geolocation");
      map?.panTo([current.longitude, current.latitude]);
    });
  } catch {
    useDefaultOrigin("failed", attempt);
  }
}

function useDefaultOrigin(
  state: Extract<MapLocationState, "outside" | "failed">,
  attempt = locationAttempt,
): void {
  if (attempt !== locationAttempt) return;
  locationState.value = state;
  if (props.guide?.default_origin) {
    emit("originChange", props.guide.default_origin, "default");
  }
}

function updateOriginMarker(): void {
  if (!map || !window.AMap) return;
  if (originMarker) map.remove(originMarker);
  originMarker = undefined;
  const displayedOrigin = pendingOrigin.value ?? props.origin;
  if (!displayedOrigin) return;
  const content = document.createElement("span");
  content.className = "zoo-map__origin-marker";
  content.classList.toggle("is-current", displayedOrigin.name === "当前位置");
  content.classList.toggle("is-preview", pendingOrigin.value !== null);
  content.textContent = displayedOrigin.name === "当前位置" ? "我" : "起";
  originMarker = new window.AMap.Marker({
    position: [displayedOrigin.longitude, displayedOrigin.latitude],
    content,
    offset: new window.AMap.Pixel(-18, -18),
    title: displayedOrigin.name,
  });
  map.add(originMarker);
}

function updateRouteOverlay(): void {
  if (!map || !window.AMap) return;
  removeRouteOverlays();

  for (const leg of routeLegs.value) {
    const path = leg.path.map(
      (point) => [point.longitude, point.latitude] as [number, number],
    );
    const shuttle = leg.mode === "shuttle";
    const halo = new window.AMap.Polyline({
      path,
      strokeColor: AMAP_COLORS.paper,
      strokeWeight: shuttle ? 11 : 12,
      strokeOpacity: 0.92,
      lineJoin: "round",
      lineCap: "round",
      zIndex: 148,
    });
    const line = new window.AMap.Polyline({
      path,
      strokeColor: shuttle ? AMAP_COLORS.activeShuttle : AMAP_COLORS.walking,
      strokeWeight: shuttle ? 6 : 7,
      strokeOpacity: 1,
      strokeStyle: "solid",
      lineJoin: "round",
      lineCap: "round",
      showDir: true,
      zIndex: 150,
    });
    routeOverlays.push(halo, line);
    map.add(halo);
    map.add(line);
  }

  fitRouteOverlays();
}

function fitRouteOverlays(): void {
  if (!map) return;
  const visibleLines = routeOverlays.filter((_, index) => index % 2 === 1);
  if (!visibleLines.length) return;
  const mapHeight = mapContainer.value?.clientHeight ?? 0;
  const compact = mapHeight > 0 && mapHeight <= 260;
  const edgePadding = compact ? 24 : 84;
  const bottomPadding = !compact && activePanel.value === "route"
    ? Math.max(84, Math.round(mapHeight * 0.55) + 16)
    : edgePadding;
  map.setFitView(
    visibleLines,
    false,
    [edgePadding, edgePadding, bottomPadding, edgePadding],
  );
}

function removeRouteOverlays(): void {
  if (!map) return;
  for (const overlay of routeOverlays) map.remove(overlay);
  routeOverlays = [];
}

function staticLegPoints(points: { longitude: number; latitude: number }[]): string {
  return points
    .map((point) => {
      const position = staticMapPosition(point.longitude, point.latitude);
      return `${position.x.toFixed(1)},${position.y.toFixed(1)}`;
    })
    .join(" ");
}

function staticMapPosition(longitude: number, latitude: number): { x: number; y: number } {
  if (!props.guide) return { x: 512, y: 320 };
  const center = project(
    props.guide.center.longitude,
    props.guide.center.latitude,
    props.guide.zoom,
  );
  const target = project(longitude, latitude, props.guide.zoom);
  return {
    x: Math.min(1024, Math.max(0, 512 + target.x - center.x)),
    y: Math.min(640, Math.max(0, 320 + target.y - center.y)),
  };
}

function createAmapBounds(
  AMap: AmapGlobal,
  path: [number, number][],
  paddingRatio = 0,
): AmapBounds | undefined {
  const bounds = polygonBounds(
    path.map(([longitude, latitude]) => ({ longitude, latitude })),
  );
  if (!bounds) return undefined;
  const longitudePadding =
    (bounds.northEast.longitude - bounds.southWest.longitude) * paddingRatio;
  const latitudePadding =
    (bounds.northEast.latitude - bounds.southWest.latitude) * paddingRatio;
  return new AMap.Bounds(
    [
      bounds.southWest.longitude - longitudePadding,
      bounds.southWest.latitude - latitudePadding,
    ],
    [
      bounds.northEast.longitude + longitudePadding,
      bounds.northEast.latitude + latitudePadding,
    ],
  );
}

function outsideMaskPath(path: [number, number][]): [number, number][][] {
  const bounds = polygonBounds(
    path.map(([longitude, latitude]) => ({ longitude, latitude })),
  );
  if (!bounds) return [path];
  const longitudePadding =
    (bounds.northEast.longitude - bounds.southWest.longitude) * 3;
  const latitudePadding =
    (bounds.northEast.latitude - bounds.southWest.latitude) * 3;
  const outer: [number, number][] = [
    [bounds.southWest.longitude - longitudePadding, bounds.southWest.latitude - latitudePadding],
    [bounds.northEast.longitude + longitudePadding, bounds.southWest.latitude - latitudePadding],
    [bounds.northEast.longitude + longitudePadding, bounds.northEast.latitude + latitudePadding],
    [bounds.southWest.longitude - longitudePadding, bounds.northEast.latitude + latitudePadding],
  ];
  return [outer, [...path].reverse()];
}

function markerStyle(point: MapPoint, pointIndex: number): Record<string, string> {
  if (!props.guide) return {};
  const center = project(props.guide.center.longitude, props.guide.center.latitude, props.guide.zoom);
  const target = project(point.longitude, point.latitude, props.guide.zoom);
  const left = 50 + ((target.x - center.x) / 1024) * 100;
  const top = 50 + ((target.y - center.y) / 640) * 100;
  const duplicateIndex = props.guide.points
    .slice(0, pointIndex)
    .filter(
      (candidate) =>
        candidate.longitude === point.longitude && candidate.latitude === point.latitude,
    ).length;
  return {
    "--marker-left": `${Math.min(92, Math.max(8, left))}%`,
    "--marker-top": `${Math.min(94, Math.max(6, top))}%`,
    "--marker-shift": `${duplicateIndex * 32}px`,
  };
}

function facilityMarkerStyle(point: FacilityPoint): Record<string, string> {
  if (!props.guide) return {};
  const position = staticMapPosition(point.longitude, point.latitude);
  return {
    "--facility-left": `${(position.x / 10.24).toFixed(2)}%`,
    "--facility-top": `${(position.y / 6.4).toFixed(2)}%`,
  };
}

function project(longitude: number, latitude: number, zoom: number): { x: number; y: number } {
  const worldSize = 256 * 2 ** zoom;
  const sinLatitude = Math.sin((latitude * Math.PI) / 180);
  return {
    x: ((longitude + 180) / 360) * worldSize,
    y:
      (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) *
      worldSize,
  };
}

function retry(): void {
  imageFailed.value = false;
  interactiveFailed.value = false;
  emit("retry");
}

interface AmapMarker {
  getContent(): HTMLButtonElement;
}

interface AmapOverlay {
  show?(): void;
  hide?(): void;
}
interface AmapBounds {}

interface AmapMapClickEvent {
  lnglat: { getLng(): number; getLat(): number };
}

interface AmapGeolocationResult {
  position?: { getLng(): number; getLat(): number };
}

interface AmapGeolocation {
  getCurrentPosition(
    callback: (status: "complete" | "error", result: AmapGeolocationResult) => void,
  ): void;
}

interface AmapMap {
  add(markers: AmapMarker[] | AmapMarker | AmapOverlay): void;
  remove(overlay: AmapMarker | AmapOverlay): void;
  destroy(): void;
  on(event: "complete", handler: () => void): void;
  on(event: "click", handler: (event: AmapMapClickEvent) => void): void;
  on(event: "zoomend", handler: () => void): void;
  getZoom(): number;
  panTo(position: [number, number]): void;
  setBounds(bounds: AmapBounds, immediately: boolean, padding: number[]): void;
  setFitView(overlays: AmapOverlay[], immediately: boolean, padding: number[]): void;
  setLimitBounds(bounds: AmapBounds): void;
  resize(): void;
}

interface AmapGlobal {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AmapMap;
  Marker: new (options: Record<string, unknown>) => AmapMarker;
  Polyline: new (options: Record<string, unknown>) => AmapOverlay;
  Polygon: new (options: Record<string, unknown>) => AmapOverlay;
  Bounds: new (southWest: [number, number], northEast: [number, number]) => AmapBounds;
  Pixel: new (x: number, y: number) => object;
  Geolocation: new (options: Record<string, unknown>) => AmapGeolocation;
  plugin(name: string, callback: () => void): void;
}

declare global {
  interface Window {
    AMap?: AmapGlobal;
    _AMapSecurityConfig?: { serviceHost: string };
  }
}

let amapLoader: Promise<AmapGlobal> | undefined;

function loadAmapPlugin(AMap: AmapGlobal, name: string): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      AMap.plugin(name, resolve);
    } catch (reason) {
      reject(reason);
    }
  });
}

function loadAmap(apiKey: string, serviceHost: string): Promise<AmapGlobal> {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (amapLoader) return amapLoader;

  const proxyUrl = new URL(serviceHost, window.location.origin);
  if (proxyUrl.pathname !== "/_AMapService") {
    return Promise.reject(new Error("高德安全代理必须使用 /_AMapService 一级路由"));
  }
  window._AMapSecurityConfig = {
    serviceHost: proxyUrl.toString().replace(/\/$/, ""),
  };
  amapLoader = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.id = "amap-js-api";
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(apiKey)}`;
    script.async = false;
    const fail = (message: string) => {
      amapLoader = undefined;
      script.remove();
      reject(new Error(message));
    };
    script.onload = () =>
      window.AMap ? resolve(window.AMap) : fail("AMap 未初始化");
    script.onerror = () => fail("AMap JS API 加载失败");
    document.head.append(script);
  });
  return amapLoader;
}
</script>

<template>
  <div class="zoo-map" :aria-busy="loading">
    <div v-if="loading" class="zoo-map__loading" aria-live="polite">
      <span></span>
      <p>正在展开高德园区地图…</p>
    </div>

    <div v-else-if="error || imageFailed || !guide" class="zoo-map__error" role="status">
      <strong>地图暂时没有展开</strong>
      <p>{{ error || "高德地图图片加载失败，请稍后重试。" }}</p>
      <button type="button" @click="retry">重新加载</button>
    </div>

    <template v-else>
      <div
        class="zoo-map__canvas"
        :class="{
          'has-active-route': activeRoute,
          'has-service-layer': activeFacilityGroup !== 'none',
        }"
        :style="staticBoundaryStyle"
      >
        <template v-if="guide.js_api && !interactiveFailed">
          <div
            ref="mapContainer"
            class="zoo-map__interactive"
            role="region"
            aria-label="可拖拽和缩放的南京红山森林动物园高德地图"
          ></div>
        </template>
        <template v-else>
          <div class="zoo-map__static-stage">
          <img
            class="zoo-map__boundary-image"
            :src="guide.image_url"
            alt="南京红山森林动物园高德地图"
            width="1024"
            height="640"
            @error="imageFailed = true"
          />
          <svg
            v-if="guide.shuttle && (activeFacilityGroup === 'transport' || activeRoute?.uses_shuttle)"
            class="zoo-map__static-shuttle"
            viewBox="0 0 1024 640"
            preserveAspectRatio="none"
            aria-label="观光车环线"
          >
            <polyline :points="staticLegPoints(pathInsideBoundary(guide.shuttle.polyline))" />
          </svg>
          <svg
            v-if="routeLegs.length"
            class="zoo-map__static-route"
            viewBox="0 0 1024 640"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <g v-for="leg in routeLegs" :key="leg.id" :class="{ 'is-shuttle': leg.mode === 'shuttle' }">
              <polyline class="is-halo" :points="staticLegPoints(leg.path)" />
              <polyline class="is-route" :points="staticLegPoints(leg.path)" />
            </g>
          </svg>
          <button
            v-for="facility in visibleFacilities"
            :key="facility.id"
            class="zoo-map__facility-marker is-static"
            :class="{ 'is-route-stop': isFacilityRouteStop(facility) }"
            :data-group="facilityVisualGroup(facility.category)"
            :style="facilityMarkerStyle(facility)"
            type="button"
            :title="facility.name"
            :aria-label="`${facility.name}，${facilityLabel(facility.category)}`"
            @click="selectFacility(facility)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path :d="facilityIconPath(facility.category)" />
            </svg>
          </button>
          <button
            v-for="(point, index) in guide.points"
            :key="point.site"
            class="zoo-map__marker"
            :class="{
              'is-active': selectedSite === point.site,
              'is-route-stop': displayedRouteSites.includes(point.site),
              'is-label-left': markerLabelLeft(point, index),
            }"
            :style="markerStyle(point, index)"
            type="button"
            :aria-label="`查看${point.site}，${point.animal_count}种动物`"
            :aria-pressed="selectedSite === point.site"
            @click="selectVenue(point.site)"
          >
            <span class="zoo-map__marker-number">{{ markerNumber(point) }}</span>
            <span class="zoo-map__marker-site-name">{{ point.site }}</span>
            <span
              v-if="routeAnimals(point.site).length"
              class="zoo-map__animal-stack"
              aria-hidden="true"
            >
              <AnimalPhoto
                v-for="(animal, animalIndex) in routeAnimals(point.site)"
                :key="animal.name"
                class="zoo-map__animal-circle"
                :animal="animal"
                :variant="animalIndex"
              />
            </span>
          </button>
          </div>
        </template>

        <div class="zoo-map__status" aria-live="polite">
          <strong v-if="activeRoute">{{ activeRoute.name }}</strong>
          <strong v-else>{{ routeSites.length ? `${routeSites.length} 个场馆待规划` : "浏览园区" }}</strong>
          <span>{{ activeRoute ? `${durationLabel} · ${activeRoute.sites.length} 站` : locationStatus }}</span>
        </div>

        <button
          class="zoo-map__locate"
          type="button"
          :disabled="!guide.js_api || interactiveFailed || locationState === 'locating'"
          :aria-label="locationState === 'locating' ? '正在定位' : '重新定位'"
          @click="locateVisitor"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="4" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
          </svg>
        </button>

        <div v-if="!activePanel" class="zoo-map__quick-actions" aria-label="地图操作">
          <button type="button" @click="toggleServicesPanel">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 12h3M11 12h9M4 17h8M16 17h4M14 5v4M7 10v4M12 15v4" /></svg>
            附近服务
          </button>
          <button
            type="button"
            :class="{ 'is-active': settingOrigin }"
            :disabled="!guide.js_api || interactiveFailed"
            @click="toggleManualOrigin"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s6-5 6-11a6 6 0 1 0-12 0c0 6 6 11 6 11Z" /><circle cx="12" cy="10" r="2" /></svg>
            {{ settingOrigin ? "取消选起点" : "设置起点" }}
          </button>
          <button v-if="activeRoute" type="button" @click="toggleRoutePanel">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h9" /></svg>
            路线详情
          </button>
        </div>

        <section v-if="activePanel" class="zoo-map__sheet" :aria-label="activePanel === 'services' ? '选择园区服务' : activePanel === 'route' ? '路线详情' : activePanel === 'origin' ? '选择路线起点' : '地点详情'">
          <button class="zoo-map__sheet-close" type="button" aria-label="关闭地图信息" @click="activePanel === 'origin' ? cancelManualOrigin() : closePanel()">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>

          <template v-if="activePanel === 'services'">
            <div class="zoo-map__sheet-heading">
              <strong>在地图上找服务</strong>
            </div>
            <div class="zoo-map__service-options">
              <button
                v-for="group in facilityGroups"
                :key="group.id"
                type="button"
                :data-group="group.id"
                :class="{ 'is-active': activeFacilityGroup === group.id }"
                :aria-pressed="activeFacilityGroup === group.id"
                @click="selectFacilityGroup(group.id)"
              >
                {{ group.label }}
              </button>
              <button
                type="button"
                :class="{ 'is-active': activeFacilityGroup === 'none' }"
                :aria-pressed="activeFacilityGroup === 'none'"
                @click="selectFacilityGroup('none')"
              >
                关闭服务点
              </button>
            </div>
          </template>

          <template v-else-if="activePanel === 'origin'">
            <div class="zoo-map__sheet-heading">
              <strong>{{ pendingOrigin ? "已选好起点" : "点击地图选择起点" }}</strong>
              <span>{{ pendingOrigin ? "确认后将从这里重新规划路线。" : "请在园区范围内点按你所在的位置。" }}</span>
            </div>
            <div class="zoo-map__origin-actions">
              <button type="button" @click="locateVisitor">使用自动定位</button>
              <button type="button" :disabled="!pendingOrigin" @click="confirmManualOrigin">从这里出发</button>
            </div>
          </template>

          <template v-else-if="activePanel === 'route'">
            <div class="zoo-map__sheet-heading">
              <strong>{{ activeRoute?.name }}</strong>
              <span>{{ durationLabel }} · {{ activeRoute?.sites.length }} 站</span>
            </div>
            <ol class="zoo-map__sheet-route" aria-label="路线分段用时">
              <li v-for="(leg, index) in routeLegs" :key="`summary-${leg.id}`">
                <span>{{ index + 1 }}</span>
                <p><strong>{{ leg.from_name }} → {{ leg.to_name }}</strong><small>{{ Math.round(leg.distance_meters / 10) * 10 }} 米</small></p>
                <em>{{ leg.mode === "shuttle" ? "观光车" : "步行" }} {{ leg.minutes }} 分钟</em>
              </li>
            </ol>
            <div v-if="amapNavigationTarget" class="zoo-map__amap-action">
              <button
                type="button"
                aria-describedby="amap-navigation-note"
                @click="openInAmap"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M14 5h5v5M19 5l-8 8" />
                  <path d="M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
                </svg>
                {{ amapNavigationTarget.label }}
              </button>
              <small id="amap-navigation-note">高德会重新计算路线，结果可能略有不同</small>
            </div>
          </template>

          <template v-else>
            <span
              v-if="selectedFacility"
              class="zoo-map__sheet-icon"
              :data-group="facilityVisualGroup(selectedFacility.category)"
              aria-hidden="true"
            >
              <svg viewBox="0 0 24 24"><path :d="facilityIconPath(selectedFacility.category)" /></svg>
            </span>
            <div class="zoo-map__sheet-place">
              <strong>{{ selectedFacility?.name || selectedPoint?.poi_name }}</strong>
              <span v-if="selectedFacility">
                {{ facilityLabel(selectedFacility.category) }} ·
                {{ selectedFacility.nearby ? `${selectedFacility.nearby}附近 · ` : "" }}{{ selectedFacility.address }}
              </span>
              <span v-else-if="selectedPoint">{{ selectedPoint.address }} · {{ selectedPoint.animal_count }} 种动物</span>
            </div>
            <button
              v-if="selectedPoint && !selectedFacility && !settingOrigin"
              class="zoo-map__route-action"
              type="button"
              :class="{ 'is-selected': selectedPointInRoute }"
              @click="toggleSelectedPointRoute"
            >
              {{ selectedPointInRoute ? "移出路线" : "加入路线" }}
            </button>
          </template>
        </section>

        <small v-if="!guide.js_api" class="zoo-map__attribution">
          地图来自 {{ guide.provider }} ·
          <a :href="guide.boundary.source_url" target="_blank" rel="noreferrer">{{ guide.boundary.attribution }}</a>
        </small>
      </div>
    </template>
  </div>
</template>
