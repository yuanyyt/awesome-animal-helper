<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchAnimals } from "./api";
import AnimalCard from "./components/AnimalCard.vue";
import AnimalDetailDialog from "./components/AnimalDetailDialog.vue";
import ForestPlaceholder from "./components/ForestPlaceholder.vue";
import GuideIllustration from "./components/GuideIllustration.vue";
import SearchDialog from "./components/SearchDialog.vue";
import SiteFilter from "./components/SiteFilter.vue";
import type { AnimalDetail, AnimalListResponse } from "./types";

const data = ref<AnimalListResponse>();
const selectedSite = ref("");
const selectedAnimal = ref<AnimalDetail | null>(null);
const searchOpen = ref(false);
const loading = ref(true);
const error = ref("");
let controller: AbortController | undefined;
let lastTrigger: HTMLElement | null = null;

onMounted(() => {
  void loadAnimals();
  window.addEventListener("keydown", handleGlobalShortcut);
});

onBeforeUnmount(() => {
  controller?.abort();
  window.removeEventListener("keydown", handleGlobalShortcut);
});

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
  <header class="site-nav">
    <p class="site-nav__edition">FOREST FIELD GUIDE · NANJING · HONGSHAN</p>
    <div class="site-nav__inner">
      <a class="site-nav__brand" href="#top" aria-label="红山动物指南首页">
        <strong>红山动物志</strong>
      </a>
      <nav class="site-nav__links" aria-label="主要导航">
        <a href="#venues">场馆漫游</a>
        <a href="#animals">动物图鉴</a>
        <button class="search-pill" type="button" aria-label="搜索动物，快捷键 Control 或 Command K" @click="openSearch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg>
          <span>搜索动物</span><kbd>⌘ K</kbd>
        </button>
      </nav>
    </div>
  </header>

  <main id="top">
    <section class="guide-hero" aria-labelledby="intro-title">
      <div class="guide-hero__copy">
        <p class="guide-hero__kicker">
          {{ data?.total ?? "—" }} 位动物邻居 · {{ data?.sites.length ?? "—" }} 座场馆 · 一次慢慢认识
        </p>
        <h1 id="intro-title">在城市的森林里，认识每一位邻居。</h1>
        <p class="guide-hero__lede">
          从一座场馆出发，听听动物们的故事。这里整理了它们的栖息地、食性、行为和保护状态，也留下一些值得带回家的有趣发现。
        </p>
        <div class="guide-hero__actions">
          <a class="button-link is-primary" href="#animals">开始看动物</a>
          <a class="button-link" href="#venues">按场馆逛</a>
        </div>
        <p class="guide-hero__note">◆ 动物资料整理自 Wikipedia 与 Wikidata</p>
      </div>
      <div class="guide-hero__art">
        <GuideIllustration />
      </div>
    </section>

    <section id="venues" class="venue-section" aria-labelledby="venues-title">
      <div class="section-heading">
        <h2 id="venues-title">从一座场馆，开始今天的漫游。</h2>
        <p>选择场馆，看看住在那里的动物。它们也可能在园内拥有不止一个家。</p>
      </div>
      <SiteFilter
        :sites="data?.sites ?? []"
        :selected="selectedSite"
        :loading="loading"
        @change="changeSite"
      />
    </section>

    <section id="animals" class="animals-section" aria-labelledby="animals-title">
      <div class="section-heading">
        <h2 id="animals-title">{{ selectedSite ? `${selectedSite}的动物` : "动物观察名册" }}</h2>
        <p v-if="data">{{ data.filtered_count }} 位动物，点击卡片展开介绍。</p>
      </div>

      <div v-if="loading" class="animal-grid" aria-label="正在加载动物资料" aria-live="polite">
        <div v-for="index in 6" :key="index" class="animal-skeleton"><span></span><i></i><i></i></div>
      </div>

      <div v-else-if="error" class="state-panel is-error" role="alert">
        <ForestPlaceholder :variant="2" />
        <h3>名册暂时合上了</h3>
        <p>{{ error }}</p>
        <button type="button" @click="loadAnimals()">重新打开</button>
      </div>

      <div v-else-if="!data?.items.length" class="state-panel">
        <ForestPlaceholder :variant="1" />
        <h3>这里还没有匹配的动物</h3>
        <p>换一个场馆，或者使用顶部搜索。</p>
        <button type="button" @click="changeSite('')">查看全部</button>
      </div>

      <div v-else class="animal-grid">
        <AnimalCard
          v-for="(animal, index) in data.items"
          :key="animal.name"
          :animal="animal"
          :index="index"
          @select="openAnimal(animal, $event)"
        />
      </div>
    </section>
  </main>

  <footer class="site-footer" aria-label="页脚">
    <p class="site-footer__statement">每一次驻足，都是认识另一种生命的开始。</p>
    <div class="site-footer__meta">
      <span>南京红山森林动物园导览 · 动物资料来自公开来源</span>
      <a href="#top">回到页首 ↑</a>
    </div>
  </footer>

  <SearchDialog :open="searchOpen" @close="closeSearch" @select="chooseSearchResult" />
  <AnimalDetailDialog :animal="selectedAnimal" @close="closeAnimal" />
</template>
