<script setup lang="ts">
import type { AnimalDetail } from "../types";

defineProps<{ animals: AnimalDetail[] }>();
const emit = defineEmits<{ remove: [name: string] }>();
</script>

<template>
  <div v-if="animals.length" class="animal-selection-chips" aria-label="已选择的动物">
    <span v-for="animal in animals" :key="animal.name" class="animal-selection-chip">
      {{ animal.name }}
      <button
        type="button"
        :aria-label="`从路线中删除${animal.name}`"
        @click="emit('remove', animal.name)"
      >
        <span aria-hidden="true">×</span>
      </button>
    </span>
  </div>
</template>
