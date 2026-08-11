<script setup lang="ts">
import type { AnimalDetail } from "../types";
import AnimalSelectionChips from "./AnimalSelectionChips.vue";

defineProps<{ animals: AnimalDetail[] }>();
const emit = defineEmits<{
  remove: [name: string];
  plan: [];
}>();
</script>

<template>
  <aside class="animal-route-dock" aria-label="动物路线规划">
    <div class="animal-route-dock__copy">
      <span>今天想看的邻居</span>
      <AnimalSelectionChips :animals="animals" @remove="emit('remove', $event)" />
    </div>
    <button type="button" :disabled="!animals.length" @click="emit('plan')">
      规划路线
      <span aria-hidden="true">→</span>
    </button>
  </aside>
</template>
