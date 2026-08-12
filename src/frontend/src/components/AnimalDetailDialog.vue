<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import type { AnimalDetail } from "../types";
import AnimalPhoto from "./AnimalPhoto.vue";
import AnimalStories from "./AnimalStories.vue";
import ExpandableText from "./ExpandableText.vue";

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

type ProfileFieldKey =
  | "habitat"
  | "distribution"
  | "diet"
  | "behavior"
  | "reproduction"
  | "conservation_status";

const fields: Array<{ key: ProfileFieldKey; label: string }> = [
  { key: "habitat", label: "栖息地" },
  { key: "distribution", label: "分布" },
  { key: "diet", label: "食性" },
  { key: "behavior", label: "行为" },
  { key: "reproduction", label: "繁殖" },
  { key: "conservation_status", label: "濒危等级" },
];
const compactFieldLength = 32;

const visibleFields = computed(() => {
  if (!props.animal) return [];
  return fields.flatMap((field) => {
    const text = props.animal?.[field.key]?.trim();
    const textLength = text ? Array.from(text.replace(/\s/g, "")).length : 0;
    return text ? [{ ...field, text, compact: textLength <= compactFieldLength }] : [];
  });
});

const visibleFunFacts = computed(
  () => props.animal?.fun_facts.map((fact) => fact.trim()).filter(Boolean) ?? [],
);

const visibleSites = computed(
  () => props.animal?.sites.filter((site) => site.trim() && site.trim() !== props.animal?.name) ?? [],
);

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
            <p v-if="visibleSites.length">{{ visibleSites.join(" · ") }}</p>
            <h2 id="detail-title">{{ animal.name }}</h2>
            <span v-if="animal.scientific_name?.trim()">{{ animal.scientific_name.trim() }}</span>
          </div>
          <button class="detail-dialog__close" type="button" aria-label="关闭动物介绍" @click="emit('close')">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
          </button>
        </header>

        <figure class="detail-dialog__illustration">
          <AnimalPhoto :animal="animal" :variant="animal.name.length" />
        </figure>

        <div class="detail-dialog__body">
          <AnimalStories
            v-if="animal.wiki_fact_count"
            :animal="animal"
            :active="true"
          />

          <section v-if="visibleFunFacts.length" class="detail-dialog__fun-facts">
            <h3>趣味事实</h3>
            <ul>
              <li v-for="fact in visibleFunFacts" :key="fact">
                <ExpandableText :text="fact" />
              </li>
            </ul>
          </section>

          <section
            v-for="field in visibleFields"
            :key="field.key"
            class="detail-dialog__profile-field"
            :class="{ 'is-compact': field.compact }"
          >
            <h3>{{ field.label }}</h3>
            <ExpandableText :text="field.text" />
          </section>
        </div>

        <footer v-if="animal.source_url" class="detail-dialog__footer">
          <a :href="animal.source_url" target="_blank" rel="noopener noreferrer">
            查看 Wikipedia 来源 ↗
          </a>
        </footer>
      </article>
    </dialog>
  </Teleport>
</template>
