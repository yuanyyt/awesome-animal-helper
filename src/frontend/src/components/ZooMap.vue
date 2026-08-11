<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { resolveAnimalImage } from "../animalImages";
import { isPointInPolygon, polygonBounds } from "../mapGeometry";
import type {
  AnimalDetail,
  FacilityCategory,
  FacilityPoint,
  MapGuide,
  MapNamedLocation,
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
  activeRoute: RouteOption | null;
  loading: boolean;
  error: string;
}>();

type MapPointLike = { longitude: number; latitude: number };

// AMap requires hexadecimal overlay colors. These match the design tokens but
// avoid passing unsupported oklch() values to different browser renderers.
const AMAP_COLORS = {
  paper: "#f8f2df",
  accent: "#2b641d",
  walking: "#d45f36",
  shuttle: "#157f86",
  activeShuttle: "#008f99",
} as const;

const emit = defineEmits<{
  select: [site: string];
  routeToggle: [site: string];
  originChange: [origin: MapNamedLocation];
  retry: [];
}>();

const imageFailed = ref(false);
const interactiveFailed = ref(false);
const interactiveReady = ref(false);
const settingOrigin = ref(false);
type LocationState = "idle" | "locating" | "inside" | "outside" | "failed" | "manual";
const locationState = ref<LocationState>("idle");
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
let locationAttempt = 0;
let automaticLocationAttempted = false;
const selectedPoint = computed(() =>
  props.guide?.points.find((point) => point.site === props.selectedSite),
);
const selectedFacility = ref<FacilityPoint | null>(null);
type FacilityGroup = "common" | "refreshment" | "family" | "transport" | "none";
const activeFacilityGroup = ref<FacilityGroup>("common");
const facilityGroups: {
  id: FacilityGroup;
  label: string;
  categories: FacilityCategory[];
}[] = [
  {
    id: "common",
    label: "常用服务",
    categories: ["entrance", "visitor_center", "tour_bus_station"],
  },
  {
    id: "refreshment",
    label: "休息补给",
    categories: ["drinking_water", "restaurant", "coffee", "shopping"],
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
      "metro", "bus_terminal", "train_station", "parking", "ticket_office",
      "bag_storage", "mobility_rental", "police", "smoking_area",
    ],
  },
  { id: "none", label: "隐藏服务", categories: [] },
];
const activeFacilityCategories = computed(
  () => new Set(
    facilityGroups.find((group) => group.id === activeFacilityGroup.value)?.categories ?? [],
  ),
);
const visibleFacilities = computed(() =>
  (props.guide?.facilities ?? []).filter((facility) =>
    activeFacilityCategories.value.has(facility.category),
  ),
);
const displayedRouteSites = computed(() => props.activeRoute?.sites ?? props.routeSites);
const durationLabel = computed(() =>
  props.activeRoute ? `约 ${props.activeRoute.total_minutes} 分钟` : "",
);
const locationStatus = computed(() => {
  if (settingOrigin.value) return "请在园区地图内点击新的起点";
  const labels: Record<LocationState, string> = {
    idle: props.origin ? `路线起点：${props.origin.name}` : "等待确认路线起点",
    locating: "正在确认你在园里的位置…",
    inside: "已定位到园内，将从当前位置出发",
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
  () => props.guide,
  (guide) => {
    if (guide) void initializeInteractiveMap();
  },
  { immediate: true },
);

watch(
  () => props.routeSites,
  () => updateInteractiveMarkers(props.selectedSite),
  { deep: true },
);

watch(
  () => props.activeRoute,
  () => {
    updateInteractiveMarkers(props.selectedSite);
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
  () => props.selectedSite,
  (site) => {
    const point = props.guide?.points.find((candidate) => candidate.site === site);
    if (site) selectedFacility.value = null;
    updateInteractiveMarkers(site);
    if (point && map) map.panTo([point.longitude, point.latitude]);
  },
);

onBeforeUnmount(() => destroyInteractiveMap());

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
  if (map && mapBounds) {
    map.setBounds(mapBounds, true, [24, 24, 24, 24]);
    if (mapLimitBounds) map.setLimitBounds(mapLimitBounds);
  }
  interactiveReady.value = true;
  updateRouteOverlay();
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
  selectedFacility.value = null;
  emit("select", site);
  emit("routeToggle", site);
}

function createFacilityMarkers(): void {
  if (!map || !window.AMap || !props.guide) return;
  const AMap = window.AMap;
  facilityMarkers = props.guide.facilities.map((facility) => {
    const content = document.createElement("button");
    content.type = "button";
    content.className = "zoo-map__facility-marker";
    content.textContent = facilityIcon(facility.category);
    content.title = facility.name;
    content.setAttribute("aria-label", `${facility.name}，${facilityLabel(facility.category)}`);
    content.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedFacility.value = facility;
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
}

function updateFacilityVisibility(): void {
  for (const { marker, facility } of facilityMarkers) {
    marker.getContent().hidden = !activeFacilityCategories.value.has(facility.category);
  }
}

function facilityIcon(category: FacilityCategory): string {
  const icons: Record<FacilityCategory, string> = {
    metro: "地", bus_terminal: "巴", train_station: "站", visitor_center: "服",
    entrance: "门", bag_storage: "存", ticket_office: "票", parking: "P",
    smoking_area: "烟", drinking_water: "水", tour_bus_station: "车",
    mobility_rental: "借", police: "警", shopping: "店", restaurant: "餐",
    coffee: "咖", toilet: "卫", nursing_room: "婴", family_toilet: "亲",
  };
  return icons[category];
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

function updateInteractiveMarkers(site: string): void {
  for (const marker of amapMarkers) {
    const button = marker.getContent();
    const markerSite = button.dataset.site ?? "";
    const routeIndex = displayedRouteSites.value.indexOf(markerSite);
    const active = markerSite === site;
    const routeSite = routeIndex >= 0;
    button.classList.toggle("is-active", active);
    button.classList.toggle("is-route-stop", routeSite);
    button.classList.toggle("is-label-visible", (map?.getZoom() ?? 0) >= 17);
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
  const labelsVisible = (map?.getZoom() ?? 0) >= 17;
  for (const marker of amapMarkers) {
    marker.getContent().classList.toggle("is-label-visible", labelsVisible);
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
  if (!settingOrigin.value) return;
  locationAttempt += 1;
  locationState.value = "manual";
  emit("originChange", {
    name: "地图选定起点",
    longitude: event.lnglat.getLng(),
    latitude: event.lnglat.getLat(),
  });
  settingOrigin.value = false;
}

function toggleManualOrigin(): void {
  settingOrigin.value = !settingOrigin.value;
}

async function locateVisitor(): Promise<void> {
  const AMap = window.AMap;
  if (!AMap || !map || !props.guide) {
    useDefaultOrigin("failed");
    return;
  }

  const attempt = ++locationAttempt;
  settingOrigin.value = false;
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
      emit("originChange", { name: "当前位置", ...current });
      map?.panTo([current.longitude, current.latitude]);
    });
  } catch {
    useDefaultOrigin("failed", attempt);
  }
}

function useDefaultOrigin(
  state: Extract<LocationState, "outside" | "failed">,
  attempt = locationAttempt,
): void {
  if (attempt !== locationAttempt) return;
  locationState.value = state;
  if (props.guide?.default_origin) emit("originChange", props.guide.default_origin);
}

function updateOriginMarker(): void {
  if (!map || !window.AMap) return;
  if (originMarker) map.remove(originMarker);
  originMarker = undefined;
  if (!props.origin) return;
  const content = document.createElement("span");
  content.className = "zoo-map__origin-marker";
  content.classList.toggle("is-current", props.origin.name === "当前位置");
  content.textContent = props.origin.name === "当前位置" ? "我" : "起";
  originMarker = new window.AMap.Marker({
    position: [props.origin.longitude, props.origin.latitude],
    content,
    offset: new window.AMap.Pixel(-18, -18),
    title: props.origin.name,
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

  const visibleLines = routeOverlays.filter((_, index) => index % 2 === 1);
  if (visibleLines.length) map.setFitView(visibleLines, false, [84, 84, 84, 84]);
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

interface AmapOverlay {}
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
      <div class="zoo-map__planner-tools">
        <div class="zoo-map__planner-copy">
          <p v-if="activeRoute"><strong>{{ activeRoute.name }}</strong> · {{ durationLabel }} · {{ activeRoute.sites.length }} 站</p>
          <p v-else><strong>{{ routeSites.length }}</strong> 个场馆已加入路线</p>
          <small aria-live="polite">{{ locationStatus }}</small>
        </div>
        <div class="zoo-map__origin-actions">
          <button
            type="button"
            :disabled="!guide.js_api || interactiveFailed || locationState === 'locating'"
            @click="locateVisitor"
          >
            {{ locationState === "locating" ? "定位中…" : "重新定位" }}
          </button>
          <button
            type="button"
            :class="{ 'is-active': settingOrigin }"
            :disabled="!guide.js_api || interactiveFailed"
            @click="toggleManualOrigin"
          >
            {{ settingOrigin ? "取消设置起点" : "在地图上设置起点" }}
          </button>
        </div>
        <div class="zoo-map__facility-filters" aria-label="地图设施图层">
          <button
            v-for="group in facilityGroups"
            :key="group.id"
            type="button"
            :class="{ 'is-active': activeFacilityGroup === group.id }"
            :aria-pressed="activeFacilityGroup === group.id"
            @click="selectFacilityGroup(group.id)"
          >
            {{ group.label }}
          </button>
        </div>
      </div>
      <div
        class="zoo-map__canvas"
        :class="{ 'has-active-route': activeRoute }"
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
          <img
            class="zoo-map__boundary-image"
            :src="guide.image_url"
            alt="南京红山森林动物园高德地图"
            width="1024"
            height="640"
            @error="imageFailed = true"
          />
          <svg
            v-if="guide.shuttle"
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
            :style="facilityMarkerStyle(facility)"
            type="button"
            :title="facility.name"
            :aria-label="`${facility.name}，${facilityLabel(facility.category)}`"
            @click="selectedFacility = facility"
          >
            {{ facilityIcon(facility.category) }}
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
        </template>
      </div>

      <ol v-if="routeLegs.length" class="zoo-map__leg-summary" aria-label="路线分段用时">
        <li v-for="(leg, index) in routeLegs" :key="`summary-${leg.id}`">
          <span>{{ index + 1 }}</span>
          <p><strong>{{ leg.from_name }} → {{ leg.to_name }}</strong><small>{{ Math.round(leg.distance_meters / 10) * 10 }} 米</small></p>
          <em>{{ leg.mode === "shuttle" ? "观光车约" : "步行约" }} {{ leg.minutes }} 分钟{{ leg.estimated ? "（估算）" : "" }}</em>
        </li>
      </ol>

      <div class="zoo-map__caption" aria-live="polite">
        <span v-if="selectedFacility" class="zoo-map__caption-icon" aria-hidden="true">
          {{ facilityIcon(selectedFacility.category) }}
        </span>
        <div>
          <p>{{ selectedFacility?.name || selectedPoint?.poi_name || "点按场馆或服务点查看详情" }}</p>
          <span v-if="selectedFacility">
            {{ facilityLabel(selectedFacility.category) }} · {{ selectedFacility.address }}
          </span>
          <span v-else-if="selectedPoint">
            {{ selectedPoint.address }} · {{ selectedPoint.animal_count }} 种动物
          </span>
          <span v-else>琥珀色圆点为场馆，方形标记为当前服务分类</span>
        </div>
        <button
          v-if="selectedFacility"
          class="zoo-map__caption-close"
          type="button"
          aria-label="关闭设施信息"
          @click="selectedFacility = null"
        >×</button>
        <strong v-else-if="selectedPoint">下方查看馆内动物 ↓</strong>
        <small v-else>
          地图来自 {{ guide.provider }} ·
          <a :href="guide.boundary.source_url" target="_blank" rel="noreferrer">{{ guide.boundary.attribution }}</a>
        </small>
      </div>
    </template>
  </div>
</template>
