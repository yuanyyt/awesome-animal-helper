<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { fetchWikiIndex, fetchWikiPage } from "../api";
import type { WikiAnimalSummary, WikiIndexResponse, WikiPage } from "../types";

const props = defineProps<{ active: boolean }>();

const catalogue = ref<WikiIndexResponse>();
const results = ref<WikiIndexResponse>();
const page = ref<WikiPage>();
const selectedSite = ref("");
const query = ref("");
const loading = ref(false);
const pageLoading = ref(false);
const error = ref("");
const pageError = ref("");
let indexController: AbortController | undefined;
let pageController: AbortController | undefined;
let searchTimer: number | undefined;

const visibleSites = computed(() => results.value?.sites ?? []);
const totalLabel = computed(() => {
  const data = catalogue.value;
  if (!data?.total_animals) return "故事仍在整理";
  return `${data.total_animals} 位邻居 · ${data.total_facts} 条趣事`;
});

onMounted(() => {
  window.addEventListener("popstate", syncRoute);
  if (props.active) void loadCatalogue();
});

onBeforeUnmount(() => {
  indexController?.abort();
  pageController?.abort();
  if (searchTimer) window.clearTimeout(searchTimer);
  window.removeEventListener("popstate", syncRoute);
});

watch(
  () => props.active,
  (active) => {
    if (active && !catalogue.value) void loadCatalogue();
    else if (active) void syncRoute();
  },
);

async function loadCatalogue(): Promise<void> {
  indexController?.abort();
  indexController = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    catalogue.value = await fetchWikiIndex({}, indexController.signal);
    await syncRoute();
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") error.value = "动物故事暂时没有打开，请稍后重试。";
  } finally {
    loading.value = false;
  }
}

async function applyFilters(): Promise<void> {
  indexController?.abort();
  indexController = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    results.value = await fetchWikiIndex(
      { q: query.value, site: selectedSite.value },
      indexController.signal,
    );
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") error.value = "没有完成这次检索，请再试一次。";
  } finally {
    loading.value = false;
  }
}

function scheduleSearch(): void {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => void applyFilters(), 250);
}

function chooseSite(site: string): void {
  selectedSite.value = site;
  page.value = undefined;
  pageError.value = "";
  updateHash({ site });
  void applyFilters();
}

async function chooseAnimal(animal: WikiAnimalSummary, pushHistory = true): Promise<void> {
  pageController?.abort();
  pageController = new AbortController();
  pageLoading.value = true;
  pageError.value = "";
  selectedSite.value = animal.site;
  if (pushHistory) {
    updateHash({
      site: animal.site,
      scientific_name: animal.scientific_name,
      animal: animal.animal_name,
    });
  }
  try {
    page.value = await fetchWikiPage(
      animal.site,
      animal.scientific_name,
      animal.animal_name,
      pageController.signal,
    );
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") pageError.value = "这篇动物故事暂时无法打开。";
  } finally {
    pageLoading.value = false;
  }
}

async function syncRoute(): Promise<void> {
  if (!props.active || !catalogue.value) return;
  const params = routeParams();
  selectedSite.value = params.get("site") ?? "";
  query.value = params.get("q") ?? "";
  await applyFilters();
  const animalName = params.get("animal");
  const scientificName = params.get("scientific_name");
  const candidate = findAnimal(animalName, scientificName);
  if (candidate) await chooseAnimal(candidate, false);
  else page.value = undefined;
}

function findAnimal(name: string | null, scientificName: string | null): WikiAnimalSummary | undefined {
  if (!name) return undefined;
  for (const site of catalogue.value?.sites ?? []) {
    for (const group of site.scientific_groups) {
      const candidate = group.animals.find(
        (animal) => animal.animal_name === name && (!scientificName || group.scientific_name === scientificName),
      );
      if (candidate) return candidate;
    }
  }
  return undefined;
}

function showIndex(): void {
  page.value = undefined;
  pageError.value = "";
  updateHash({ site: selectedSite.value, q: query.value });
}

function retryPage(): void {
  const current = findAnimal(routeParams().get("animal"), routeParams().get("scientific_name"));
  if (current) void chooseAnimal(current);
}

function routeParams(): URLSearchParams {
  const [, search = ""] = window.location.hash.split("?", 2);
  return new URLSearchParams(search);
}

function updateHash(values: Record<string, string>): void {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  const suffix = params.size ? `?${params.toString()}` : "";
  window.history.pushState(null, "", `#wiki${suffix}`);
}
</script>

<template>
  <section class="wiki-shell" aria-labelledby="wiki-title">
    <header class="wiki-heading">
      <div>
        <h1 id="wiki-title">动物故事索引</h1>
        <p>{{ totalLabel }}</p>
      </div>
      <label class="wiki-search">
        <span>在故事里找</span>
        <span class="wiki-search__field">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg>
          <input
            v-model="query"
            type="search"
            placeholder="动物、昵称或趣事"
            :aria-busy="loading"
            @input="scheduleSearch"
          />
        </span>
      </label>
    </header>

    <div v-if="error" class="wiki-state is-error" role="alert">
      <p>{{ error }}</p>
      <button type="button" @click="loadCatalogue">重新打开</button>
    </div>

    <div v-else-if="!catalogue && loading" class="wiki-skeleton" aria-label="正在整理动物故事">
      <span v-for="item in 7" :key="item"></span>
    </div>

    <div v-else-if="catalogue" class="wiki-layout">
      <nav class="wiki-rail" aria-label="按场馆浏览">
        <button type="button" :aria-current="selectedSite ? undefined : 'true'" @click="chooseSite('')">
          全部场馆
        </button>
        <button
          v-for="site in catalogue.sites"
          :key="site.name"
          type="button"
          :aria-current="selectedSite === site.name ? 'true' : undefined"
          @click="chooseSite(site.name)"
        >
          <span>{{ site.name }}</span>
          <small>{{ site.fact_count }}</small>
        </button>
      </nav>

      <div class="wiki-index" :class="{ 'is-covered': page }" aria-live="polite">
        <p v-if="loading" class="wiki-index__loading">正在翻找故事……</p>
        <div v-else-if="!visibleSites.length" class="wiki-state">
          <p>{{ query ? `没有找到与“${query}”有关的故事。` : "故事正在整理，过些时候再来看看。" }}</p>
          <button v-if="query" type="button" @click="query = ''; scheduleSearch()">清空检索</button>
        </div>
        <section v-for="site in visibleSites" v-else :key="site.name" class="wiki-site-group">
          <header>
            <h2>{{ site.name }}</h2>
            <span>{{ site.fact_count }} 条趣事</span>
          </header>
          <div v-for="group in site.scientific_groups" :key="group.scientific_name" class="wiki-scientific-group">
            <p>{{ group.scientific_name }}</p>
            <button
              v-for="animal in group.animals"
              :key="`${animal.site}-${animal.animal_name}`"
              type="button"
              @click="chooseAnimal(animal)"
            >
              <span>
                <strong>{{ animal.animal_name }}</strong>
                <small v-if="animal.aliases.length">文中也叫 {{ animal.aliases.join("、") }}</small>
              </span>
              <span>{{ animal.fact_count }} 条 <b aria-hidden="true">↗</b></span>
            </button>
          </div>
        </section>
      </div>

      <article v-if="page || pageLoading || pageError" class="wiki-reader" aria-live="polite">
        <button class="wiki-reader__back" type="button" @click="showIndex">← 返回索引</button>
        <div v-if="pageLoading" class="wiki-reader__loading">正在打开故事……</div>
        <div v-else-if="pageError" class="wiki-state is-error">
          <p>{{ pageError }}</p>
          <button type="button" @click="retryPage">再试一次</button>
        </div>
        <template v-else-if="page">
          <header class="wiki-reader__heading">
            <p>{{ page.site }}</p>
            <h2>{{ page.animal_name }}</h2>
            <span>{{ page.scientific_name }}</span>
            <small v-if="page.aliases.length">文中昵称 · {{ page.aliases.join("、") }}</small>
          </header>
          <ol class="wiki-facts">
            <li v-for="(fact, index) in page.facts" :key="`${fact.source.url}-${index}`">
              <span>{{ String(index + 1).padStart(2, "0") }}</span>
              <div>
                <p>{{ fact.text }}</p>
                <details v-if="fact.evidence">
                  <summary>查看正文依据</summary>
                  <blockquote>{{ fact.evidence }}</blockquote>
                </details>
                <a :href="fact.source.url" target="_blank" rel="noopener noreferrer">
                  {{ fact.source.title }}<template v-if="fact.source.published_at"> · {{ fact.source.published_at }}</template> ↗
                </a>
              </div>
            </li>
          </ol>
        </template>
      </article>
    </div>
  </section>
</template>
