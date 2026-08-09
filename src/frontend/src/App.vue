<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchAnimals, fetchMapGuide } from "./api";
import AnimalDetailDialog from "./components/AnimalDetailDialog.vue";
import AnimalPhoto from "./components/AnimalPhoto.vue";
import ForestPlaceholder from "./components/ForestPlaceholder.vue";
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
          <h1 id="guide-title">今天，想先去见谁？</h1>
          <p>点按园区里的场馆，右侧会展开住在那里的动物。</p>
        </div>
        <span>导览范围由场馆点位近似生成 · 非官方园界</span>
      </header>

      <div class="guide-layout">
        <div class="guide-layout__main">
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
          <GuideChatBox
            :selected-sites="selectedRouteSites"
            :origin="routeOrigin"
            :active-route-id="activeRoute?.id ?? ''"
            @route-select="selectRoute"
          />
        </div>

        <aside class="animal-rail" aria-labelledby="animal-rail-title" aria-live="polite">
          <header class="animal-rail__heading">
            <div>
              <p>场馆动物</p>
              <h2 id="animal-rail-title">{{ selectedSite || "等待你选一站" }}</h2>
            </div>
            <strong v-if="selectedSite && data">{{ data.filtered_count }}</strong>
          </header>

          <div v-if="!selectedSite" class="animal-rail__empty">
            <ForestPlaceholder :variant="0" />
            <p>点击地图上的琥珀色点位，动物邻居会在这里排好队。</p>
          </div>
          <div v-else-if="loading" class="animal-rail__list" aria-label="正在加载场馆动物">
            <div v-for="index in 5" :key="index" class="animal-rail__skeleton"></div>
          </div>
          <div v-else-if="error" class="animal-rail__empty is-error" role="alert">
            <p>{{ error }}</p>
            <button type="button" @click="loadAnimals()">重新打开名册</button>
          </div>
          <div v-else-if="!data?.items.length" class="animal-rail__empty">
            <p>这座场馆暂时没有匹配的动物资料。</p>
          </div>
          <div v-else class="animal-rail__list">
            <button
              v-for="(animal, index) in data.items"
              :key="animal.name"
              class="animal-rail__animal"
              type="button"
              @click="openAnimal(animal, $event)"
            >
              <span class="animal-rail__visual"><AnimalPhoto :animal="animal" :variant="index" /></span>
              <span class="animal-rail__copy">
                <strong>{{ animal.name }}</strong>
                <small>{{ animal.scientific_name || "学名待补充" }}</small>
                <em v-if="animal.conservation_status">{{ animal.conservation_status }}</em>
              </span>
              <span aria-hidden="true">↗</span>
            </button>
          </div>
        </aside>
      </div>
    </section>
  </main>

  <SearchDialog :open="searchOpen" @close="closeSearch" @select="chooseSearchResult" />
  <AnimalDetailDialog :animal="selectedAnimal" @close="closeAnimal" />
</template>
