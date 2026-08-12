<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

import { fetchAnimals, fetchWikiIndex } from "../api";
import type { AnimalDetail, AnimalDetailSection, WikiIndexResponse } from "../types";

const props = defineProps<{
  open: boolean;
  animals: AnimalDetail[];
}>();
const emit = defineEmits<{
  close: [];
  select: [animal: AnimalDetail, section: AnimalDetailSection];
}>();

interface SearchResult {
  animal: AnimalDetail;
  section: AnimalDetailSection;
}

const dialog = ref<HTMLDialogElement>();
const input = ref<HTMLInputElement>();
const query = ref("");
const results = ref<SearchResult[]>([]);
const activeIndex = ref(0);
const loading = ref(false);
const error = ref("");
let timer: number | undefined;
let controller: AbortController | undefined;
let pendingSelection: SearchResult | undefined;

watch(
  () => props.open,
  async (open) => {
    if (open) {
      dialog.value?.showModal();
      await nextTick();
      input.value?.focus();
      await search();
    } else if (dialog.value?.open) {
      dialog.value.close();
    }
  },
);

watch(query, () => {
  window.clearTimeout(timer);
  timer = window.setTimeout(search, 220);
});

onBeforeUnmount(() => {
  window.clearTimeout(timer);
  controller?.abort();
});

async function search(): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const trimmedQuery = query.value.trim();
    const [animalsResult, wikiResult] = await Promise.allSettled([
      fetchAnimals({ q: trimmedQuery }, controller.signal),
      trimmedQuery
        ? fetchWikiIndex({ q: trimmedQuery }, controller.signal)
        : Promise.resolve<WikiIndexResponse | null>(null),
    ]);
    const wikiIndex = wikiResult.status === "fulfilled" ? wikiResult.value : null;
    if (animalsResult.status === "rejected" && !wikiIndex) {
      throw animalsResult.reason;
    }

    const basicAnimals = animalsResult.status === "fulfilled" ? animalsResult.value.items : [];
    const merged = new Map<string, SearchResult>(
      basicAnimals.map((animal) => [animal.name, { animal, section: "profile" }]),
    );

    if (wikiIndex) {
      for (const site of wikiIndex.sites) {
        for (const group of site.scientific_groups) {
          for (const summary of group.animals) {
            if (merged.has(summary.animal_name)) continue;
            const animal = props.animals.find((item) => item.name === summary.animal_name);
            if (animal) {
              merged.set(animal.name, { animal, section: "stories" });
            }
          }
        }
      }
    }

    results.value = [...merged.values()].slice(0, 12);
    activeIndex.value = 0;
  } catch (reason) {
    if ((reason as Error).name !== "AbortError") {
      error.value = "暂时找不到动物资料，请稍后再试。";
    }
  } finally {
    loading.value = false;
  }
}

function handleKeys(event: KeyboardEvent): void {
  if (!results.value.length) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    activeIndex.value = (activeIndex.value + 1) % results.value.length;
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value = (activeIndex.value - 1 + results.value.length) % results.value.length;
  } else if (event.key === "Enter") {
    event.preventDefault();
    choose(results.value[activeIndex.value]);
  }
}

function choose(result: SearchResult): void {
  pendingSelection = result;
  if (dialog.value?.open) {
    dialog.value.close();
    window.setTimeout(completeSelection, 0);
  } else {
    completeSelection();
  }
}

function completeSelection(): void {
  if (!pendingSelection) return;
  emit("select", pendingSelection.animal, pendingSelection.section);
  pendingSelection = undefined;
}
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="search-dialog"
      aria-labelledby="search-title"
      @cancel.prevent="emit('close')"
      @close="completeSelection"
      @click.self="emit('close')"
    >
      <div class="search-dialog__panel">
        <div class="search-dialog__field">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <label id="search-title" class="visually-hidden" for="animal-search">搜索动物和园内趣事</label>
          <input
            id="animal-search"
            ref="input"
            v-model="query"
            type="search"
            placeholder="动物名、学名或故事关键词…"
            autocomplete="off"
            @keydown="handleKeys"
          />
          <button type="button" class="search-dialog__esc" aria-label="关闭搜索" @click="emit('close')">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
          </button>
        </div>

        <div class="search-dialog__results" aria-live="polite">
          <p v-if="loading" class="search-dialog__message">正在翻阅动物名册…</p>
          <p v-else-if="error" class="search-dialog__message is-error">{{ error }}</p>
          <p v-else-if="!results.length" class="search-dialog__message">没有找到，换个动物名或故事关键词试试。</p>
          <template v-else>
            <p class="search-dialog__group">找到 {{ results.length }} 个结果</p>
            <button
              v-for="(result, index) in results"
              :key="result.animal.name"
              class="search-dialog__result"
              :class="{ 'is-active': index === activeIndex }"
              type="button"
              @mouseenter="activeIndex = index"
              @click="choose(result)"
            >
              <span>
                <strong>{{ result.animal.name }}</strong>
                <span>{{ result.animal.scientific_name || "学名待补充" }}</span>
                <em v-if="result.section === 'stories'">园内趣事匹配</em>
              </span>
              <small>{{ result.animal.sites.join(" · ") }}</small>
            </button>
          </template>
        </div>
        <div class="search-dialog__foot" aria-hidden="true">
          <span>↑ ↓ 选择</span><span>Enter 打开</span><span>Esc 关闭</span>
        </div>
      </div>
    </dialog>
  </Teleport>
</template>
