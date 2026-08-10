<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchAnimals, fetchMapGuide } from "./api";
import AnimalDetailDialog from "./components/AnimalDetailDialog.vue";
import GuideChatBox from "./components/GuideChatBox.vue";
import GuideIllustration from "./components/GuideIllustration.vue";
import SearchDialog from "./components/SearchDialog.vue";
import ZooMap from "./components/ZooMap.vue";
import type {
  AnimalDetail,
  AnimalListResponse,
  MapGuide,
  MapNamedLocation,
  RouteOption,
} from "./types";

const data = ref<AnimalListResponse>();
const selectedSite = ref("");
const selectedAnimal = ref<AnimalDetail | null>(null);
const searchOpen = ref(false);
const loading = ref(true);
const error = ref("");
const mapGuide = ref<MapGuide>();
const mapLoading = ref(true);
const mapError = ref("");
const selectedRouteSites = ref<string[]>([]);
const routeOrigin = ref<MapNamedLocation | null>(null);
const activeRoute = ref<RouteOption | null>(null);
type AppPage = "intro" | "guide";
const activePage = ref<AppPage>(pageFromLocation());
let controller: AbortController | undefined;
let mapController: AbortController | undefined;
let lastTrigger: HTMLElement | null = null;

onMounted(() => {
  void loadAnimals();
  void loadMap();
  window.addEventListener("keydown", handleGlobalShortcut);
  window.addEventListener("popstate", syncPageFromLocation);
});

onBeforeUnmount(() => {
  controller?.abort();
  mapController?.abort();
  window.removeEventListener("keydown", handleGlobalShortcut);
  window.removeEventListener("popstate", syncPageFromLocation);
});

function pageFromLocation(): AppPage {
  return ["#guide", "#map", "#venues", "#animals"].includes(window.location.hash)
    ? "guide"
    : "intro";
}

function syncPageFromLocation(): void {
  activePage.value = pageFromLocation();
}

function showPage(page: AppPage): void {
  if (activePage.value === page) return;
  activePage.value = page;
  window.history.pushState(null, "", page === "guide" ? "#guide" : "#home");
}

async function loadMap(): Promise<void> {
  mapController?.abort();
  mapController = new AbortController();
  mapLoading.value = true;
  mapError.value = "";
  try {
    mapGuide.value = await fetchMapGuide(mapController.signal);
    routeOrigin.value ??= mapGuide.value.default_origin;
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") {
      mapError.value = "园区地图暂时没有打开，请稍后重试。";
    }
  } finally {
    mapLoading.value = false;
  }
}

async function loadAnimals(site = selectedSite.value): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    data.value = await fetchAnimals({ site }, controller.signal);
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") {
      error.value = "动物名册暂时没有打开，请确认后端服务已经启动。";
    }
  } finally {
    loading.value = false;
  }
}

function changeSite(site: string): void {
  selectedSite.value = site;
  void loadAnimals(site);
}

function toggleRouteSite(site: string): void {
  selectedRouteSites.value = selectedRouteSites.value.includes(site)
    ? selectedRouteSites.value.filter((item) => item !== site)
    : [...selectedRouteSites.value, site];
  activeRoute.value = null;
}

function setRouteOrigin(origin: MapNamedLocation): void {
  routeOrigin.value = origin;
  activeRoute.value = null;
}

function selectRoute(route: RouteOption): void {
  activeRoute.value = route;
}

function openAnimal(animal: AnimalDetail, event?: MouseEvent): void {
  lastTrigger = event?.currentTarget as HTMLElement | null;
  selectedAnimal.value = animal;
}

function closeAnimal(): void {
  selectedAnimal.value = null;
  window.setTimeout(() => lastTrigger?.focus(), 0);
}

function openSearch(): void {
  lastTrigger = document.activeElement as HTMLElement | null;
  searchOpen.value = true;
}

function closeSearch(): void {
  searchOpen.value = false;
  window.setTimeout(() => lastTrigger?.focus(), 0);
}

async function chooseSearchResult(animal: AnimalDetail): Promise<void> {
  searchOpen.value = false;
  await nextTick();
  await new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
  selectedAnimal.value = animal;
}

function handleGlobalShortcut(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
}
</script>

<template>
  <header class="site-nav" :class="{ 'is-guide': activePage === 'guide' }">
    <p class="site-nav__edition">FOREST FIELD GUIDE · NANJING · HONGSHAN</p>
    <div class="site-nav__inner">
      <button class="site-nav__brand" type="button" aria-label="返回红山动物指南首页" @click="showPage('intro')">
        <strong>红山动物志</strong>
      </button>
      <nav class="site-nav__links" aria-label="主要导航">
        <div class="page-tabs" aria-label="页面切换">
          <button type="button" :aria-current="activePage === 'intro' ? 'page' : undefined" @click="showPage('intro')">首页</button>
          <button type="button" :aria-current="activePage === 'guide' ? 'page' : undefined" @click="showPage('guide')">园区导览</button>
        </div>
        <button class="search-pill" type="button" aria-label="搜索动物，快捷键 Control 或 Command K" @click="openSearch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg>
          <span>搜索动物</span><kbd>⌘ K</kbd>
        </button>
      </nav>
    </div>
  </header>

  <main id="top" class="app-pages">
    <section
      class="page-panel guide-hero"
      :class="{ 'is-active': activePage === 'intro' }"
      :aria-hidden="activePage !== 'intro'"
      :inert="activePage !== 'intro'"
      aria-labelledby="intro-title"
    >
      <div class="guide-hero__copy">
        <p class="guide-hero__kicker">
          {{ data?.total ?? "—" }} 位动物邻居 · {{ data?.sites.length ?? "—" }} 座场馆 · 一次慢慢认识
        </p>
        <h1 id="intro-title">在城市的森林里，认识每一位邻居。</h1>
        <p class="guide-hero__lede">
          从一座场馆出发，听听动物们的故事。这里整理了它们的栖息地、食性、行为和保护状态，也留下一些值得带回家的有趣发现。
        </p>
        <div class="guide-hero__actions">
          <button class="button-link is-primary" type="button" @click="showPage('guide')">打开园区地图</button>
          <button class="button-link" type="button" @click="openSearch">搜索动物邻居</button>
        </div>
        <p class="guide-hero__note">◆ 动物资料整理自 Wikipedia 与 Wikidata</p>
      </div>
      <div class="guide-hero__art">
        <GuideIllustration />
      </div>
    </section>

    <section
      id="guide"
      class="page-panel guide-workspace"
      :class="{ 'is-active': activePage === 'guide' }"
      :aria-hidden="activePage !== 'guide'"
      :inert="activePage !== 'guide'"
      aria-labelledby="guide-title"
    >
      <header class="guide-workspace__heading">
        <div>
          <h1 id="guide-title">今天，想问什么？</h1>
          <p>问一句，导览员会带来路线、地图或动物故事。</p>
        </div>
        <span>AGNO 对话 · 高德路线 · 本地动物资料</span>
      </header>

      <GuideChatBox
        :selected-sites="selectedRouteSites"
        :selected-site="selectedSite"
        :animals="data?.items ?? []"
        :animals-loading="loading"
        :animals-error="error"
        :origin="routeOrigin"
        :active-route-id="activeRoute?.id ?? ''"
        @route-select="selectRoute"
        @animal-select="openAnimal"
        @animals-retry="loadAnimals()"
      >
        <template #map>
          <ZooMap
            :guide="mapGuide"
            :selected-site="selectedSite"
            :route-sites="selectedRouteSites"
            :origin="routeOrigin"
            :active-route="activeRoute"
            :loading="mapLoading"
            :error="mapError"
            @select="changeSite"
            @route-toggle="toggleRouteSite"
            @origin-change="setRouteOrigin"
            @retry="loadMap"
          />
        </template>
      </GuideChatBox>
    </section>
  </main>

  <SearchDialog :open="searchOpen" @close="closeSearch" @select="chooseSearchResult" />
  <AnimalDetailDialog :animal="selectedAnimal" @close="closeAnimal" />
</template>
