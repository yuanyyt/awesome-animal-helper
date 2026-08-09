<script setup lang="ts">
import type { SiteSummary } from "../types";

defineProps<{
  sites: SiteSummary[];
  selected: string;
  loading: boolean;
}>();

const emit = defineEmits<{ change: [site: string] }>();
</script>

<template>
  <div class="site-filter" aria-label="按场馆筛选">
    <button
      class="site-filter__chip"
      :class="{ 'is-active': !selected }"
      type="button"
      :aria-pressed="!selected"
      :disabled="loading"
      @click="emit('change', '')"
    >
      全部动物
    </button>
    <button
      v-for="site in sites"
      :key="site.name"
      class="site-filter__chip"
      :class="{ 'is-active': selected === site.name }"
      type="button"
      :aria-pressed="selected === site.name"
      :disabled="loading"
      @click="emit('change', site.name)"
    >
      {{ site.name }} <span>{{ site.animal_count }}</span>
    </button>
  </div>
</template>

