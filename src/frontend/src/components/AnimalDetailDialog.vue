<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import type { AnimalDetail } from "../types";
import AnimalPhoto from "./AnimalPhoto.vue";
import AnimalStories from "./AnimalStories.vue";

const props = withDefaults(
  defineProps<{
    animal: AnimalDetail | null;
    focusSection?: "profile" | "stories";
    focusRequest?: number;
  }>(),
  {
    focusSection: "profile",
    focusRequest: 0,
  },
);
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();
const panel = ref<HTMLElement>();

const fields: Array<{ key: keyof AnimalDetail; label: string }> = [
  { key: "taxonomy", label: "分类" },
  { key: "habitat", label: "栖息地" },
  { key: "distribution", label: "分布" },
  { key: "diet", label: "食性" },
  { key: "behavior", label: "行为" },
  { key: "reproduction", label: "繁殖" },
  { key: "conservation_status", label: "保护状态" },
];

watch(
  () => props.animal,
  async (animal) => {
    if (animal) {
      if (!dialog.value?.open) dialog.value?.showModal();
      await nextTick();
      panel.value?.focus({ preventScroll: true });
      panel.value?.scrollTo({ top: 0 });
      if (props.focusSection === "stories") scrollToStories();
    } else if (dialog.value?.open) {
      dialog.value.close();
    }
  },
);

watch(
  () => props.focusRequest,
  () => {
    if (props.animal && props.focusSection === "stories") scrollToStories();
  },
);

function scrollToStories(): void {
  window.requestAnimationFrame(() => {
    panel.value?.querySelector<HTMLElement>("#animal-stories")?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  });
}
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="detail-dialog"
      :aria-labelledby="animal ? 'detail-title' : undefined"
      @cancel.prevent="emit('close')"
      @click.self="emit('close')"
    >
      <article v-if="animal" ref="panel" class="detail-dialog__panel" tabindex="-1">
        <header class="detail-dialog__header">
          <div>
            <p>{{ animal.sites.join(" · ") }}</p>
            <h2 id="detail-title">{{ animal.name }}</h2>
            <span>{{ animal.scientific_name || "学名待补充" }}</span>
          </div>
          <button class="detail-dialog__close" type="button" aria-label="关闭动物介绍" @click="emit('close')">×</button>
        </header>

        <figure class="detail-dialog__illustration">
          <AnimalPhoto :animal="animal" :variant="animal.name.length" />
          <figcaption>动物图册 · {{ animal.name }}</figcaption>
        </figure>

        <div class="detail-dialog__body">
          <section v-for="field in fields" :key="field.key">
            <h3>{{ field.label }}</h3>
            <p>{{ animal[field.key] || "资料待补充" }}</p>
          </section>

          <section>
            <h3>趣味事实</h3>
            <ul v-if="animal.fun_facts.length">
              <li v-for="fact in animal.fun_facts" :key="fact">{{ fact }}</li>
            </ul>
            <p v-else>资料待补充</p>
          </section>

          <AnimalStories
            v-if="animal.wiki_fact_count"
            :animal="animal"
            :active="true"
          />
        </div>

        <footer class="detail-dialog__footer">
          <span v-if="animal.data_status !== 'success'">部分资料仍在补充</span>
          <span v-else>资料已整理</span>
          <a v-if="animal.source_url" :href="animal.source_url" target="_blank" rel="noopener noreferrer">
            查看 Wikipedia 来源 ↗
          </a>
        </footer>
      </article>
    </dialog>
  </Teleport>
</template>
