<script setup lang="ts">
import type { AnimalDetail } from "../types";
import AnimalPhoto from "./AnimalPhoto.vue";

defineProps<{ animal: AnimalDetail; index: number; selected: boolean }>();
const emit = defineEmits<{
  select: [event: MouseEvent];
  toggle: [];
}>();
</script>

<template>
  <article class="animal-card">
    <button class="animal-card__button" type="button" @click="emit('select', $event)">
      <span class="animal-card__visual">
        <AnimalPhoto :animal="animal" :variant="index" />
        <span v-if="animal.conservation_status" class="animal-card__status">
          {{ animal.conservation_status }}
        </span>
        <span v-if="animal.wiki_fact_count" class="animal-card__stories">
          {{ animal.wiki_fact_count }} 条园内趣事
        </span>
      </span>
      <span class="animal-card__content">
        <span class="animal-card__heading">
          <strong>{{ animal.name }}</strong>
          <span aria-hidden="true">↗</span>
        </span>
        <span class="animal-card__sites">{{ animal.sites.join(" · ") }}</span>
      </span>
    </button>
    <button
      class="animal-card__route"
      :class="{ 'is-selected': selected }"
      type="button"
      :aria-pressed="selected"
      :aria-label="selected ? `从路线中移除${animal.name}` : `想看${animal.name}`"
      @click="emit('toggle')"
    >
      <span aria-hidden="true">{{ selected ? "✓" : "+" }}</span>
      {{ selected ? "已添加" : "加入路线" }}
    </button>
  </article>
</template>
