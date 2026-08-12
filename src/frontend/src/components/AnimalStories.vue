<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { fetchWikiIndex, fetchWikiPage } from "../api";
import type { AnimalDetail, WikiAnimalSummary, WikiPage } from "../types";

const props = defineProps<{
  animal: AnimalDetail;
  active: boolean;
}>();

const pageCache = new Map<string, WikiPage[]>();
const pages = ref<WikiPage[]>([]);
const loading = ref(false);
const error = ref("");
let controller: AbortController | undefined;

const factCount = computed(() => pages.value.reduce((total, page) => total + page.facts.length, 0));

watch(
  () => [props.animal.name, props.active] as const,
  ([, active]) => {
    if (active && props.animal.wiki_fact_count > 0) void loadStories();
  },
  { immediate: true },
);

onBeforeUnmount(() => controller?.abort());

async function loadStories(force = false): Promise<void> {
  const animalName = props.animal.name;
  if (!force && pageCache.has(animalName)) {
    pages.value = pageCache.get(animalName) ?? [];
    return;
  }

  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  loading.value = true;
  error.value = "";
  pages.value = [];

  try {
    const index = await fetchWikiIndex({ q: animalName }, signal);
    const matches = exactMatches(index.sites.flatMap((site) => site.scientific_groups.flatMap((group) => group.animals)));
    const loadedPages = await Promise.all(
      matches.map((item) => fetchWikiPage(item.site, item.scientific_name, item.animal_name, signal)),
    );
    if (props.animal.name !== animalName) return;
    pageCache.set(animalName, loadedPages);
    pages.value = loadedPages;
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") {
      error.value = "园内趣事暂时没有打开，请再试一次。";
    }
  } finally {
    if (props.animal.name === animalName) loading.value = false;
  }
}

function exactMatches(items: WikiAnimalSummary[]): WikiAnimalSummary[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.animal_name !== props.animal.name) return false;
    const key = `${item.site}\u0000${item.scientific_name}\u0000${item.animal_name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
</script>

<template>
  <section id="animal-stories" class="animal-stories" aria-labelledby="animal-stories-title">
    <header class="animal-stories__heading">
      <div>
        <h3 id="animal-stories-title">园内趣事</h3>
      </div>
      <span v-if="factCount">{{ factCount }} 条</span>
      <span v-else>{{ animal.wiki_fact_count }} 条待打开</span>
    </header>

    <div v-if="loading" class="animal-stories__skeleton" aria-label="正在打开园内趣事" aria-live="polite">
      <span v-for="item in 3" :key="item"></span>
    </div>

    <div v-else-if="error" class="animal-stories__state is-error" role="alert">
      <p>{{ error }}</p>
      <button type="button" @click="loadStories(true)">重新打开</button>
    </div>

    <div v-else-if="!pages.length" class="animal-stories__state">
      <p>这位邻居的故事仍在整理。</p>
    </div>

    <div v-else class="animal-stories__pages">
      <section v-for="page in pages" :key="`${page.site}-${page.scientific_name}`" class="animal-story-group">
        <header>
          <h4>{{ page.site }}</h4>
          <span v-if="page.aliases.length">文中也叫 {{ page.aliases.join("、") }}</span>
        </header>

        <ol>
          <li v-for="(fact, index) in page.facts" :key="`${fact.source.url}-${index}`">
            <span aria-hidden="true">{{ String(index + 1).padStart(2, "0") }}</span>
            <div>
              <p>{{ fact.text }}</p>
              <a
                :href="fact.source.url"
                :aria-label="`阅读原文：${fact.source.title}`"
                target="_blank"
                rel="noopener noreferrer"
              >
                <span>{{ fact.source.title }}</span>
                <small v-if="fact.source.published_at">{{ fact.source.published_at.slice(0, 10) }}</small>
                <b aria-hidden="true">↗</b>
              </a>
            </div>
          </li>
        </ol>
      </section>
    </div>
  </section>
</template>
