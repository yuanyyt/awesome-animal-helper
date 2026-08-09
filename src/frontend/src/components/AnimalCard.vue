<script setup lang="ts">
import ForestPlaceholder from "./ForestPlaceholder.vue";
import type { AnimalDetail } from "../types";

defineProps<{ animal: AnimalDetail; index: number }>();
const emit = defineEmits<{ select: [event: MouseEvent] }>();
</script>

<template>
  <article class="animal-card">
    <button class="animal-card__button" type="button" @click="emit('select', $event)">
      <span class="animal-card__visual">
        <ForestPlaceholder :variant="index" />
        <span v-if="animal.conservation_status" class="animal-card__status">
          {{ animal.conservation_status }}
        </span>
      </span>
      <span class="animal-card__content">
        <span class="animal-card__heading">
          <strong>{{ animal.name }}</strong>
          <span aria-hidden="true">↗</span>
        </span>
        <span class="animal-card__scientific">
          {{ animal.scientific_name || "学名待补充" }}
        </span>
        <span class="animal-card__sites">{{ animal.sites.join(" · ") }}</span>
      </span>
    </button>
  </article>
</template>

