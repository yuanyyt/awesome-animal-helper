<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

import { fetchAnimals } from "../api";
import type { AnimalDetail } from "../types";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{
  close: [];
  select: [animal: AnimalDetail];
}>();

const dialog = ref<HTMLDialogElement>();
const input = ref<HTMLInputElement>();
const query = ref("");
const results = ref<AnimalDetail[]>([]);
const activeIndex = ref(0);
const loading = ref(false);
const error = ref("");
let timer: number | undefined;
let controller: AbortController | undefined;
let pendingSelection: AnimalDetail | undefined;

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
    const response = await fetchAnimals({ q: query.value }, controller.signal);
    results.value = response.items.slice(0, 12);
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

function choose(animal: AnimalDetail): void {
  pendingSelection = animal;
  if (dialog.value?.open) {
    dialog.value.close();
    window.setTimeout(completeSelection, 0);
  } else {
    completeSelection();
  }
}

function completeSelection(): void {
  if (!pendingSelection) return;
  emit("select", pendingSelection);
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
          <label id="search-title" class="visually-hidden" for="animal-search">搜索动物</label>
          <input
            id="animal-search"
            ref="input"
            v-model="query"
            type="search"
            placeholder="输入动物名或学名…"
            autocomplete="off"
            @keydown="handleKeys"
          />
          <button type="button" class="search-dialog__esc" @click="emit('close')">Esc</button>
        </div>

        <div class="search-dialog__results" aria-live="polite">
          <p v-if="loading" class="search-dialog__message">正在翻阅动物名册…</p>
          <p v-else-if="error" class="search-dialog__message is-error">{{ error }}</p>
          <p v-else-if="!results.length" class="search-dialog__message">没有找到，换个名字试试。</p>
          <template v-else>
            <p class="search-dialog__group">找到 {{ results.length }} 个结果</p>
            <button
              v-for="(animal, index) in results"
              :key="animal.name"
              class="search-dialog__result"
              :class="{ 'is-active': index === activeIndex }"
              type="button"
              @mouseenter="activeIndex = index"
              @click="choose(animal)"
            >
              <span><strong>{{ animal.name }}</strong>{{ animal.scientific_name || "学名待补充" }}</span>
              <small>{{ animal.sites.join(" · ") }}</small>
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
