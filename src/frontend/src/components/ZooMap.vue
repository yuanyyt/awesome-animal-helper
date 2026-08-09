<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import type { MapGuide, MapNamedLocation, MapPoint, RouteOption } from "../types";

const props = defineProps<{
  guide?: MapGuide;
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
let routeLine: AmapOverlay | undefined;
let originMarker: AmapMarker | undefined;
let readinessTimer: number | undefined;
const selectedPoint = computed(() =>
  props.guide?.points.find((point) => point.site === props.selectedSite),
);

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
  () => updateRouteOverlay(),
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
    map = new AMap.Map(mapContainer.value, {
      center: [props.guide.center.longitude, props.guide.center.latitude],
      zoom: props.guide.zoom,
      viewMode: "2D",
      resizeEnable: true,
    });
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
  interactiveReady.value = true;
}

function createMarkerButton(point: MapPoint, index: number): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "zoo-map__amap-marker";
  button.textContent = String(index + 1);
  button.setAttribute("aria-label", `查看${point.site}，${point.animal_count}种动物`);
  button.setAttribute("aria-pressed", "false");
  return button;
}

function updateInteractiveMarkers(site: string): void {
  for (const marker of amapMarkers) {
    const button = marker.getContent();
    const active = button.getAttribute("aria-label")?.startsWith(`查看${site}，`) ?? false;
    const routeSite = props.routeSites.some((item) =>
      button.getAttribute("aria-label")?.startsWith(`查看${item}，`),
    );
    button.classList.toggle("is-active", active);
    button.classList.toggle("is-route-stop", routeSite);
    button.setAttribute("aria-pressed", String(active));
  }
}

function destroyInteractiveMap(): void {
  if (readinessTimer !== undefined) window.clearTimeout(readinessTimer);
  readinessTimer = undefined;
  amapMarkers = [];
  routeLine = undefined;
  originMarker = undefined;
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
  if (routeLine) map.remove(routeLine);
  routeLine = undefined;
  const path = props.activeRoute?.polyline.map(
    (point) => [point.longitude, point.latitude] as [number, number],
  );
  if (!path?.length) return;
  routeLine = new window.AMap.Polyline({
    path,
    strokeColor: "#b84a12",
    strokeWeight: 7,
    strokeOpacity: 0.9,
    lineJoin: "round",
    lineCap: "round",
    showDir: true,
  });
  map.add(routeLine);
  map.setFitView([routeLine], false, [60, 60, 60, 60]);
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
    "--marker-left": `${Math.min(96, Math.max(4, left))}%`,
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
  setFitView(overlays: AmapOverlay[], immediately: boolean, padding: number[]): void;
}

interface AmapGlobal {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AmapMap;
  Marker: new (options: Record<string, unknown>) => AmapMarker;
  Polyline: new (options: Record<string, unknown>) => AmapOverlay;
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
  window._AMapSecurityConfig = {
    serviceHost: new URL(serviceHost, window.location.origin).toString().replace(/\/$/, ""),
  };
  amapLoader = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.id = "amap-js-api";
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(apiKey)}`;
    script.async = true;
    script.onload = () => (window.AMap ? resolve(window.AMap) : reject(new Error("AMap 未初始化")));
    script.onerror = () => {
      amapLoader = undefined;
      script.remove();
      reject(new Error("AMap JS API 加载失败"));
    };
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
        <p><strong>{{ routeSites.length }}</strong> 个场馆已加入路线</p>
        <button
          type="button"
          :class="{ 'is-active': settingOrigin }"
          :disabled="!guide.js_api || interactiveFailed"
          @click="settingOrigin = !settingOrigin"
        >
          {{ settingOrigin ? "请点击地图设置起点" : "在地图上设置起点" }}
        </button>
      </div>
      <div class="zoo-map__canvas">
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
            :src="guide.image_url"
            alt="南京红山森林动物园高德地图"
            width="1024"
            height="640"
            @error="imageFailed = true"
          />
          <button
            v-for="(point, index) in guide.points"
            :key="point.site"
            class="zoo-map__marker"
            :class="{
              'is-active': selectedSite === point.site,
              'is-route-stop': routeSites.includes(point.site),
            }"
            :style="markerStyle(point, index)"
            type="button"
            :aria-label="`查看${point.site}，${point.animal_count}种动物`"
            :aria-pressed="selectedSite === point.site"
            @click="emit('select', point.site); emit('routeToggle', point.site)"
          >
            <span>{{ index + 1 }}</span>
          </button>
        </template>
      </div>

      <div class="zoo-map__caption">
        <div>
          <p>{{ selectedPoint ? selectedPoint.poi_name : "选择地图上的琥珀色点位" }}</p>
          <span v-if="selectedPoint">
            {{ selectedPoint.address }} · {{ selectedPoint.animal_count }} 种动物
          </span>
          <span v-else>高德已收录 {{ guide.points.length }} 个园内场馆点位</span>
        </div>
        <a v-if="selectedPoint" href="#animals">查看馆内动物 ↓</a>
        <small v-else>地图数据来自 {{ guide.provider }}</small>
      </div>
      <ol class="zoo-map__places" aria-label="高德地图场馆索引">
        <li v-for="(point, index) in guide.points" :key="`place-${point.site}`">
          <button
            type="button"
            :class="{
              'is-active': selectedSite === point.site,
              'is-route-stop': routeSites.includes(point.site),
            }"
            :aria-label="`选择${point.site}场馆`"
            :aria-pressed="selectedSite === point.site"
            @click="emit('select', point.site); emit('routeToggle', point.site)"
          >
            <span>{{ index + 1 }}</span>
            {{ point.site }}
          </button>
        </li>
      </ol>
    </template>
  </div>
</template>
