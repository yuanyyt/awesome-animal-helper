<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchAnimals, fetchMapGuide } from "./api";
import AnimalDetailDialog from "./components/AnimalDetailDialog.vue";
import AnimalCard from "./components/AnimalCard.vue";
import AnimalRouteDock from "./components/AnimalRouteDock.vue";
import GuideChatBox from "./components/GuideChatBox.vue";
import GuideIllustration from "./components/GuideIllustration.vue";
import MobileBottomNav from "./components/MobileBottomNav.vue";
import SearchDialog from "./components/SearchDialog.vue";
import SiteFilter from "./components/SiteFilter.vue";
import ZooMap from "./components/ZooMap.vue";
import type {
  AnimalDetail,
  AnimalDetailSection,
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
const detailSection = ref<AnimalDetailSection>("profile");
const detailFocusRequest = ref(0);
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
const activePageLabel = computed(() => {
  const labels: Record<AppPage, string> = {
    intro: "首页",
    animals: "动物邻居",
    guide: "园区导览",
  };
  return labels[activePage.value];
});
const showBackToTop = ref(false);
const pageScrollPositions: Record<AppPage, number> = {
  intro: 0,
  animals: 0,
  guide: 0,
};
let controller: AbortController | undefined;
let mapController: AbortController | undefined;
let lastTrigger: HTMLElement | null = null;
let previousScrollRestoration: ScrollRestoration | undefined;

onMounted(() => {
  previousScrollRestoration = window.history.scrollRestoration;
  window.history.scrollRestoration = "manual";
  normalizeLegacyWikiHash();
  activePage.value = pageFromLocation();
  void loadAnimals();
  void loadMap();
  window.addEventListener("keydown", handleGlobalShortcut);
  window.addEventListener("popstate", syncPageFromLocation);
  window.addEventListener("scroll", updateBackToTop, { passive: true });
  restorePageScroll(activePage.value);
  updateBackToTop();
});

onBeforeUnmount(() => {
  controller?.abort();
  mapController?.abort();
  window.removeEventListener("keydown", handleGlobalShortcut);
  window.removeEventListener("popstate", syncPageFromLocation);
  window.removeEventListener("scroll", updateBackToTop);
  if (previousScrollRestoration) {
    window.history.scrollRestoration = previousScrollRestoration;
  }
});

function pageFromLocation(): AppPage {
  if (window.location.hash.startsWith("#wiki")) return "animals";
  if (window.location.hash.startsWith("#animals")) return "animals";
  if (["#guide", "#map", "#venues"].includes(window.location.hash)) return "guide";
  return "intro";
}

function syncPageFromLocation(): void {
  pageScrollPositions[activePage.value] = window.scrollY;
  normalizeLegacyWikiHash();
  activePage.value = pageFromLocation();
  syncAnimalFromLocation();
  restorePageScroll(activePage.value);
  updateBackToTop();
}

function showPage(page: AppPage): void {
  if (activePage.value === page) return;
  pageScrollPositions[activePage.value] = window.scrollY;
  activePage.value = page;
  const hashes: Record<AppPage, string> = {
    intro: "#home",
    animals: "#animals",
    guide: "#guide",
  };
  window.history.pushState(null, "", hashes[page]);
  restorePageScroll(page);
  updateBackToTop();
}

function restorePageScroll(page: AppPage): void {
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      window.scrollTo({
        top: page === "intro" ? 0 : pageScrollPositions[page],
        left: 0,
        behavior: "auto",
      });
      updateBackToTop();
    });
  });
}

function updateBackToTop(): void {
  showBackToTop.value =
    activePage.value === "animals" &&
    window.scrollY > Math.max(480, window.innerHeight * 0.75);
}

function scrollToTop(): void {
  window.scrollTo({
    top: 0,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
  });
}

function normalizeLegacyWikiHash(): void {
  if (!window.location.hash.startsWith("#wiki")) return;
  const params = hashParams();
  if (params.get("animal")) params.set("section", "stories");
  const suffix = params.size ? `?${params.toString()}` : "";
  window.history.replaceState(null, "", `#animals${suffix}`);
}

function hashParams(): URLSearchParams {
  const [, search = ""] = window.location.hash.split("?", 2);
  return new URLSearchParams(search);
}

function syncAnimalFromLocation(): void {
  if (activePage.value !== "animals" || !data.value) return;
  const params = hashParams();
  const site = params.get("site");
  if (site && data.value.sites.some((item) => item.name === site)) gallerySite.value = site;
  const animalName = params.get("animal");
  selectedAnimal.value = animalName
    ? data.value.items.find((animal) => animal.name === animalName) ?? null
    : null;
  detailSection.value = params.get("section") === "stories" ? "stories" : "profile";
  detailFocusRequest.value += 1;
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
    syncAnimalFromLocation();
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

function openAnimal(
  animal: AnimalDetail,
  event?: MouseEvent,
  section: AnimalDetailSection = "profile",
): void {
  lastTrigger = event?.currentTarget as HTMLElement | null;
  selectedAnimal.value = animal;
  detailSection.value = section;
  detailFocusRequest.value += 1;
  if (activePage.value === "animals") {
    const params = new URLSearchParams({ animal: animal.name });
    if (section === "stories") params.set("section", "stories");
    window.history.pushState({ animalDialog: true }, "", `#animals?${params.toString()}`);
  }
}

function closeAnimal(): void {
  if (activePage.value === "animals" && hashParams().has("animal")) {
    if (window.history.state?.animalDialog) window.history.back();
    else {
      selectedAnimal.value = null;
      window.history.replaceState(null, "", "#animals");
    }
  } else {
    selectedAnimal.value = null;
  }
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

async function chooseSearchResult(animal: AnimalDetail, section: AnimalDetailSection): Promise<void> {
  searchOpen.value = false;
  await nextTick();
  await new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
  openAnimal(animal, undefined, section);
}

function handleGlobalShortcut(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
}
</script>

<template>
  <a class="skip-link" href="#page-content">跳到主要内容</a>
  <header class="site-nav" :class="`is-${activePage}`">
    <div class="site-nav__inner">
      <a class="site-nav__brand" href="#home" aria-label="返回红山动物指南首页" @click.prevent="showPage('intro')">
        <strong>红山动物志</strong>
      </a>
      <strong class="site-nav__mobile-title">{{ activePageLabel }}</strong>
      <nav class="site-nav__links" aria-label="主要导航">
        <div class="page-tabs" aria-label="页面切换">
          <a href="#home" :aria-current="activePage === 'intro' ? 'page' : undefined" @click.prevent="showPage('intro')">首页</a>
          <a href="#animals" :aria-current="activePage === 'animals' ? 'page' : undefined" @click.prevent="showPage('animals')">动物邻居</a>
          <a href="#guide" :aria-current="activePage === 'guide' ? 'page' : undefined" @click.prevent="showPage('guide')">园区导览</a>
        </div>
        <button class="search-pill" type="button" aria-label="搜索动物和园内趣事，快捷键 Control 或 Command K" @click="openSearch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg>
          <span>搜索动物</span><kbd>⌘ K</kbd>
        </button>
      </nav>
    </div>
  </header>

  <main
    id="page-content"
    class="app-pages"
    :class="[`is-${activePage}`, { 'is-intro': activePage === 'intro' }]"
  >
    <section
      class="page-panel guide-hero"
      :class="{ 'is-active': activePage === 'intro' }"
      :aria-hidden="activePage !== 'intro'"
      :inert="activePage !== 'intro'"
      aria-labelledby="intro-title"
    >
      <div class="guide-hero__copy">
        <div class="guide-hero__identity" aria-label="南京红山森林动物园">
          <span class="guide-hero__mark" aria-hidden="true">
            <svg viewBox="0 0 40 40">
              <path d="M6 28.5 15.5 17l6 6.5L27 14l7 14.5H6Z" />
              <path d="M20 10.5c4.7-4.8 9-4.9 12.7-1.1-3.4 4.7-7.7 5.1-12.7 1.1Z" />
              <path d="M20 10.5c1.2 4 1.1 7.7-.3 11.2" />
            </svg>
          </span>
          <span>
            <strong>南京 · 红山森林动物园</strong>
            <small>城市里的森林动物园</small>
          </span>
        </div>
        <h1 id="intro-title">在城市的森林里，认识每一位邻居。</h1>
        <p class="guide-hero__lede">
          从动物故事到实时导览，陪你更轻松地逛红山森林动物园。
        </p>
        <div class="guide-hero__actions">
          <button class="button-link is-primary" type="button" @click="showPage('guide')">园区导览</button>
          <button class="button-link" type="button" @click="showPage('animals')">动物邻居</button>
        </div>
      </div>
      <div class="guide-hero__art">
        <GuideIllustration />
      </div>
    </section>

    <section
      id="animals"
      class="page-panel animals-page"
      :class="{
        'is-active': activePage === 'animals',
        'has-route-dock': selectedAnimals.length > 0,
      }"
      :aria-hidden="activePage !== 'animals'"
      :inert="activePage !== 'animals'"
      aria-labelledby="animals-title"
    >
      <header class="animals-page__heading">
        <div>
          <h1 id="animals-title">把想见的邻居，放进今天的路线。</h1>
          <p v-if="data">{{ data.total }} 位动物邻居，故事和档案都在这里。</p>
        </div>
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
        v-if="selectedAnimals.length"
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

  <Transition name="back-to-top">
    <button
      v-if="showBackToTop"
      class="back-to-top"
      :class="{ 'is-above-route': selectedAnimals.length > 0 }"
      type="button"
      aria-label="返回动物邻居页面顶部"
      title="返回顶部"
      @click="scrollToTop"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 11 6-6 6 6M12 5v14" />
      </svg>
      <span>顶部</span>
    </button>
  </Transition>

  <MobileBottomNav :active-page="activePage" @navigate="showPage" />

  <SearchDialog
    :open="searchOpen"
    :animals="data?.items ?? []"
    @close="closeSearch"
    @select="chooseSearchResult"
  />
  <AnimalDetailDialog
    :animal="selectedAnimal"
    :focus-section="detailSection"
    :focus-request="detailFocusRequest"
    @close="closeAnimal"
  />
</template>
