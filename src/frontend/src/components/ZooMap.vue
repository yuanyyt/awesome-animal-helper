<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { resolveAnimalImage } from "../animalImages";
import { expandedConvexHull, polygonBounds } from "../mapGeometry";
import type {
  AnimalDetail,
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
const mapContainer = ref<HTMLElement>();
let map: AmapMap | undefined;
let amapMarkers: AmapMarker[] = [];
let routeOverlays: AmapOverlay[] = [];
let routeLabelMarkers: AmapMarker[] = [];
let originMarker: AmapMarker | undefined;
let boundaryLine: AmapOverlay | undefined;
let mapBounds: AmapBounds | undefined;
let readinessTimer: number | undefined;
const selectedPoint = computed(() =>
  props.guide?.points.find((point) => point.site === props.selectedSite),
);
const displayedRouteSites = computed(() => props.activeRoute?.sites ?? props.routeSites);
const durationLabel = computed(() =>
  props.activeRoute ? `约 ${props.activeRoute.total_minutes} 分钟` : "",
);
const routeLegs = computed(() => {
  const route = props.activeRoute;
  if (!route) return [];
  const legs = route.legs
    .map((leg, index) => ({
      ...leg,
      id: `${index}-${leg.from_name}-${leg.to_name}`,
      minutes: Math.max(1, Math.ceil(leg.duration_seconds / 60)),
      path: leg.polyline,
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
const boundaryPath = computed(() => expandedConvexHull(props.guide?.points ?? [], 1.5));
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
    });
    const bounds = createAmapBounds(AMap, path);
    mapBounds = bounds;
    if (path.length >= 3) {
      boundaryLine = new AMap.Polygon({
        path: outsideMaskPath(path),
        fillColor: cssColor("--color-paper"),
        fillOpacity: 0.78,
        strokeColor: cssColor("--color-accent"),
        strokeOpacity: 0.72,
        strokeWeight: 3,
        zIndex: 20,
      });
      map.add(boundaryLine);
    }
    map.on("complete", markInteractiveMapReady);
    map.on("click", handleMapClick);
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
        offset: new AMap.Pixel(-22, -22),
        title: point.poi_name,
        zIndex: 220,
      });
      content.addEventListener("click", (event) => {
        event.stopPropagation();
        emit("select", point.site);
        emit("routeToggle", point.site);
      });
      return marker;
    });
    map.add(amapMarkers);
    updateInteractiveMarkers(props.selectedSite);
    updateOriginMarker();
    updateRouteOverlay();
  } catch {
    destroyInteractiveMap();
    interactiveFailed.value = true;
  }
}

function markInteractiveMapReady(): void {
  if (readinessTimer !== undefined) window.clearTimeout(readinessTimer);
  readinessTimer = undefined;
  if (map && mapBounds) {
    map.setBounds(mapBounds, true, [24, 24, 24, 24]);
    map.setLimitBounds(mapBounds);
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

function updateInteractiveMarkers(site: string): void {
  for (const marker of amapMarkers) {
    const button = marker.getContent();
    const markerSite = button.dataset.site ?? "";
    const routeIndex = displayedRouteSites.value.indexOf(markerSite);
    const active = markerSite === site;
    const routeSite = routeIndex >= 0;
    button.classList.toggle("is-active", active);
    button.classList.toggle("is-route-stop", routeSite);
    button.classList.toggle(
      "is-label-left",
      (routeSite ? routeIndex : Number(button.dataset.defaultIndex ?? 1) - 1) % 2 === 1,
    );
    renderMarkerContent(
      button,
      routeSite ? routeIndex + 1 : Number(button.dataset.defaultIndex ?? 0),
      routeSite ? routeAnimals(markerSite) : [],
    );
    button.setAttribute("aria-pressed", String(active));
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
  number.textContent = String(markerNumber);
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

function markerNumber(point: MapPoint, fallbackIndex: number): number {
  const routeIndex = displayedRouteSites.value.indexOf(point.site);
  return routeIndex >= 0 ? routeIndex + 1 : fallbackIndex + 1;
}

function markerLabelLeft(point: MapPoint, fallbackIndex: number): boolean {
  const routeIndex = displayedRouteSites.value.indexOf(point.site);
  return (routeIndex >= 0 ? routeIndex : fallbackIndex) % 2 === 1;
}

function destroyInteractiveMap(): void {
  if (readinessTimer !== undefined) window.clearTimeout(readinessTimer);
  readinessTimer = undefined;
  amapMarkers = [];
  routeOverlays = [];
  routeLabelMarkers = [];
  originMarker = undefined;
  boundaryLine = undefined;
  mapBounds = undefined;
  map?.destroy();
  map = undefined;
  interactiveReady.value = false;
}

function handleMapClick(event: AmapMapClickEvent): void {
  if (!settingOrigin.value) return;
  emit("originChange", {
    name: "地图选定起点",
    longitude: event.lnglat.getLng(),
    latitude: event.lnglat.getLat(),
  });
  settingOrigin.value = false;
}

function updateOriginMarker(): void {
  if (!map || !window.AMap) return;
  if (originMarker) map.remove(originMarker);
  originMarker = undefined;
  if (!props.origin) return;
  const content = document.createElement("span");
  content.className = "zoo-map__origin-marker";
  content.textContent = "起";
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
    const halo = new window.AMap.Polyline({
      path,
      strokeColor: cssColor("--color-ink"),
      strokeWeight: 17,
      strokeOpacity: 0.82,
      lineJoin: "round",
      lineCap: "round",
      zIndex: 148,
    });
    const line = new window.AMap.Polyline({
      path,
      strokeColor: cssColor("--color-danger"),
      strokeWeight: 10,
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

    const midpoint = routeMidpoint(leg.path);
    if (!midpoint) continue;
    const content = createRouteLegLabel(leg.minutes);
    const marker = new window.AMap.Marker({
      position: [midpoint.longitude, midpoint.latitude],
      content,
      offset: new window.AMap.Pixel(-42, -20),
      title: `${leg.from_name}到${leg.to_name}步行约${leg.minutes}分钟`,
      zIndex: 260,
    });
    routeLabelMarkers.push(marker);
    map.add(marker);
  }

  const visibleLines = routeOverlays.filter((_, index) => index % 2 === 1);
  if (visibleLines.length) map.setFitView(visibleLines, false, [84, 84, 84, 84]);
}

function removeRouteOverlays(): void {
  if (!map) return;
  for (const overlay of routeOverlays) map.remove(overlay);
  for (const marker of routeLabelMarkers) map.remove(marker);
  routeOverlays = [];
  routeLabelMarkers = [];
}

function createRouteLegLabel(minutes: number): HTMLElement {
  const label = document.createElement("span");
  label.className = "zoo-map__leg-label";
  const time = document.createElement("strong");
  time.textContent = `${minutes} 分钟`;
  const mode = document.createElement("small");
  mode.textContent = "步行";
  label.append(time, mode);
  return label;
}

function routeMidpoint(points: { longitude: number; latitude: number }[]) {
  if (!points.length) return null;
  if (points.length === 1) return points[0];
  const lengths = points.slice(1).map((point, index) =>
    localDistance(points[index], point),
  );
  const halfway = lengths.reduce((total, length) => total + length, 0) / 2;
  let walked = 0;
  for (let index = 0; index < lengths.length; index += 1) {
    const segment = lengths[index];
    if (walked + segment < halfway || segment === 0) {
      walked += segment;
      continue;
    }
    const ratio = (halfway - walked) / segment;
    return {
      longitude:
        points[index].longitude +
        (points[index + 1].longitude - points[index].longitude) * ratio,
      latitude:
        points[index].latitude +
        (points[index + 1].latitude - points[index].latitude) * ratio,
    };
  }
  return points.at(-1) ?? null;
}

function localDistance(
  from: { longitude: number; latitude: number },
  to: { longitude: number; latitude: number },
): number {
  const averageLatitude = ((from.latitude + to.latitude) / 2) * (Math.PI / 180);
  const longitude = (to.longitude - from.longitude) * Math.cos(averageLatitude);
  const latitude = to.latitude - from.latitude;
  return Math.hypot(longitude, latitude);
}

function staticLegPoints(points: { longitude: number; latitude: number }[]): string {
  return points
    .map((point) => {
      const position = staticMapPosition(point.longitude, point.latitude);
      return `${position.x.toFixed(1)},${position.y.toFixed(1)}`;
    })
    .join(" ");
}

function staticLegLabelStyle(
  points: { longitude: number; latitude: number }[],
  index: number,
): Record<string, string> {
  const midpoint = routeMidpoint(points);
  if (!midpoint) return {};
  const position = staticMapPosition(midpoint.longitude, midpoint.latitude);
  const left = Math.min(76, Math.max(24, position.x / 10.24));
  const top = Math.min(82, Math.max(18, position.y / 6.4));
  return {
    "--leg-left": `${left.toFixed(2)}%`,
    "--leg-top": `${top.toFixed(2)}%`,
    "--leg-nudge-y": index % 2 ? "-2.5rem" : "2.5rem",
  };
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
): AmapBounds | undefined {
  const bounds = polygonBounds(
    path.map(([longitude, latitude]) => ({ longitude, latitude })),
  );
  if (!bounds) return undefined;
  return new AMap.Bounds(
    [bounds.southWest.longitude, bounds.southWest.latitude],
    [bounds.northEast.longitude, bounds.northEast.latitude],
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

function cssColor(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
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

interface AmapMap {
  add(markers: AmapMarker[] | AmapMarker | AmapOverlay): void;
  remove(overlay: AmapMarker | AmapOverlay): void;
  destroy(): void;
  on(event: "complete", handler: () => void): void;
  on(event: "click", handler: (event: AmapMapClickEvent) => void): void;
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
}

declare global {
  interface Window {
    AMap?: AmapGlobal;
    _AMapSecurityConfig?: { serviceHost: string };
  }
}

let amapLoader: Promise<AmapGlobal> | undefined;

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
        <p v-if="activeRoute"><strong>{{ activeRoute.name }}</strong> · {{ durationLabel }} · {{ activeRoute.sites.length }} 站</p>
        <p v-else><strong>{{ routeSites.length }}</strong> 个场馆已加入路线</p>
        <button
          type="button"
          :class="{ 'is-active': settingOrigin }"
          :disabled="!guide.js_api || interactiveFailed"
          @click="settingOrigin = !settingOrigin"
        >
          {{ settingOrigin ? "请点击地图设置起点" : "在地图上设置起点" }}
        </button>
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
          <span v-if="!interactiveReady" class="zoo-map__mode">正在加载交互地图…</span>
          <span v-else class="zoo-map__mode">可拖拽 · 可缩放</span>
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
            v-if="routeLegs.length"
            class="zoo-map__static-route"
            viewBox="0 0 1024 640"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <g v-for="leg in routeLegs" :key="leg.id">
              <polyline class="is-halo" :points="staticLegPoints(leg.path)" />
              <polyline class="is-route" :points="staticLegPoints(leg.path)" />
            </g>
          </svg>
          <span
            v-for="(leg, index) in routeLegs"
            :key="`label-${leg.id}`"
            class="zoo-map__leg-label is-static"
            :style="staticLegLabelStyle(leg.path, index)"
          >
            <strong>{{ leg.minutes }} 分钟</strong>
            <small>步行</small>
          </span>
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
            @click="emit('select', point.site); emit('routeToggle', point.site)"
          >
            <span class="zoo-map__marker-number">{{ markerNumber(point, index) }}</span>
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
          <em>步行约 {{ leg.minutes }} 分钟</em>
        </li>
      </ol>

      <div class="zoo-map__caption">
        <div>
          <p>{{ selectedPoint ? selectedPoint.poi_name : "选择地图上的琥珀色点位" }}</p>
          <span v-if="selectedPoint">
            {{ selectedPoint.address }} · {{ selectedPoint.animal_count }} 种动物
          </span>
          <span v-else>高德已收录 {{ guide.points.length }} 个园内场馆点位 · 绿色轮廓为导览显示范围</span>
        </div>
        <strong v-if="selectedPoint">下方查看馆内动物 ↓</strong>
        <small v-else>地图数据来自 {{ guide.provider }}</small>
      </div>
    </template>
  </div>
</template>
