<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchAnimals, fetchMapGuide } from "./api";
import AnimalDetailDialog from "./components/AnimalDetailDialog.vue";
import AnimalCard from "./components/AnimalCard.vue";
import AnimalRouteDock from "./components/AnimalRouteDock.vue";
import GuideChatBox from "./components/GuideChatBox.vue";
import GuideIllustration from "./components/GuideIllustration.vue";
import SearchDialog from "./components/SearchDialog.vue";
import SiteFilter from "./components/SiteFilter.vue";
import ZooMap from "./components/ZooMap.vue";
import type {
  AnimalDetail,
  AnimalListResponse,
  GuideAutoRequest,
  MapGuide,
  MapNamedLocation,
  RouteOption,
} from "./types";

const data = ref<AnimalListResponse>();
const selectedSite = ref("");
const gallerySite = ref("");
const selectedAnimal = ref<AnimalDetail | null>(null);
const selectedAnimals = ref<AnimalDetail[]>([]);
const searchOpen = ref(false);
const loading = ref(true);
const error = ref("");
const mapGuide = ref<MapGuide>();
const mapLoading = ref(true);
const mapError = ref("");
const selectedRouteSites = ref<string[]>([]);
const routeOrigin = ref<MapNamedLocation | null>(null);
const activeRoute = ref<RouteOption | null>(null);
const autoRequest = ref<GuideAutoRequest | null>(null);
const galleryAnimals = computed(() => {
  const items = data.value?.items ?? [];
  return gallerySite.value
    ? items.filter((animal) => animal.sites.includes(gallerySite.value))
    : items;
});
const siteAnimals = computed(() => {
  const items = data.value?.items ?? [];
  return selectedSite.value
    ? items.filter((animal) => animal.sites.includes(selectedSite.value))
    : items;
});
type AppPage = "intro" | "animals" | "guide";
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
  if (window.location.hash === "#animals") return "animals";
  if (["#guide", "#map", "#venues"].includes(window.location.hash)) return "guide";
  return "intro";
}

function syncPageFromLocation(): void {
  activePage.value = pageFromLocation();
}

function showPage(page: AppPage): void {
  if (activePage.value === page) return;
  activePage.value = page;
  const hashes: Record<AppPage, string> = {
    intro: "#home",
    animals: "#animals",
    guide: "#guide",
  };
  window.history.pushState(null, "", hashes[page]);
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

async function loadAnimals(): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    data.value = await fetchAnimals({}, controller.signal);
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
}

function toggleAnimal(animal: AnimalDetail): void {
  selectedAnimals.value = selectedAnimals.value.some((item) => item.name === animal.name)
    ? selectedAnimals.value.filter((item) => item.name !== animal.name)
    : [...selectedAnimals.value, animal];
  activeRoute.value = null;
}

function removeAnimal(name: string): void {
  selectedAnimals.value = selectedAnimals.value.filter((animal) => animal.name !== name);
  activeRoute.value = null;
}

function planSelectedAnimals(): void {
  if (!selectedAnimals.value.length) return;
  activeRoute.value = null;
  showPage("guide");
  autoRequest.value = {
    id: (autoRequest.value?.id ?? 0) + 1,
    message: "请根据我选择的动物规划一条游览路线",
  };
}

function toggleRouteSite(site: string): void {
  selectedRouteSites.value = selectedRouteSites.value.includes(site)
    ? selectedRouteSites.value.filter((item) => item !== site)
    : [...selectedRouteSites.value, site];
  activeRoute.value = null;
}

function setRouteOrigin(origin: MapNamedLocation): void {
  const current = routeOrigin.value;
  if (
    current?.name === origin.name &&
    Math.abs(current.longitude - origin.longitude) < 1e-8 &&
    Math.abs(current.latitude - origin.latitude) < 1e-8
  ) {
    return;
  }
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
    <div class="site-nav__inner">
      <button class="site-nav__brand" type="button" aria-label="返回红山动物指南首页" @click="showPage('intro')">
        <strong>红山动物志</strong>
      </button>
      <nav class="site-nav__links" aria-label="主要导航">
        <div class="page-tabs" aria-label="页面切换">
          <button type="button" :aria-current="activePage === 'intro' ? 'page' : undefined" @click="showPage('intro')">首页</button>
          <button type="button" :aria-current="activePage === 'animals' ? 'page' : undefined" @click="showPage('animals')">动物邻居</button>
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
        <h1 id="intro-title">在城市的森林里，认识每一位邻居。</h1>
        <p class="guide-hero__lede">
          从一座场馆出发，听听动物们的故事。这里整理了它们的栖息地、食性、行为和保护状态，也留下一些值得带回家的有趣发现。
        </p>
        <div class="guide-hero__actions">
          <button class="button-link is-primary" type="button" @click="showPage('guide')">打开园区地图</button>
          <button class="button-link" type="button" @click="showPage('animals')">认识动物邻居</button>
        </div>
      </div>
      <div class="guide-hero__art">
        <GuideIllustration />
      </div>
    </section>

    <section
      id="animals"
      class="page-panel animals-page"
      :class="{ 'is-active': activePage === 'animals' }"
      :aria-hidden="activePage !== 'animals'"
      :inert="activePage !== 'animals'"
      aria-labelledby="animals-title"
    >
      <header class="animals-page__heading">
        <h1 id="animals-title">把想见的邻居，放进今天的路线。</h1>
      </header>

      <SiteFilter
        :sites="data?.sites ?? []"
        :selected="gallerySite"
        :loading="loading"
        @change="gallerySite = $event"
      />

      <p v-if="error" class="animals-page__state is-error">{{ error }}</p>
      <div v-else-if="loading" class="animal-grid" aria-label="正在打开动物图册">
        <div v-for="index in 9" :key="index" class="animal-skeleton"><span></span><i></i></div>
      </div>
      <div v-else class="animal-grid">
        <AnimalCard
          v-for="(animal, index) in galleryAnimals"
          :key="animal.name"
          :animal="animal"
          :index="index"
          :selected="selectedAnimals.some((item) => item.name === animal.name)"
          @select="openAnimal(animal, $event)"
          @toggle="toggleAnimal(animal)"
        />
      </div>

      <AnimalRouteDock
        :animals="selectedAnimals"
        @remove="removeAnimal"
        @plan="planSelectedAnimals"
      />
    </section>

    <section
      id="guide"
      class="page-panel guide-workspace"
      :class="{ 'is-active': activePage === 'guide' }"
      :aria-hidden="activePage !== 'guide'"
      :inert="activePage !== 'guide'"
      aria-label="园区导览对话"
    >
      <GuideChatBox
        :selected-sites="selectedRouteSites"
        :selected-animals="selectedAnimals"
        :selected-site="selectedSite"
        :animals="siteAnimals"
        :animals-loading="loading"
        :animals-error="error"
        :origin="routeOrigin"
        :active-route="activeRoute"
        :auto-request="autoRequest"
        @route-select="selectRoute"
        @animal-select="openAnimal"
        @animal-remove="removeAnimal"
        @animals-retry="loadAnimals()"
      >
        <template #map>
          <ZooMap
            :guide="mapGuide"
            :animals="data?.items ?? []"
            :selected-animals="selectedAnimals"
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
