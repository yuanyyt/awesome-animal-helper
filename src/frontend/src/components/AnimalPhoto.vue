<script setup lang="ts">
import { ref, watch } from "vue";

import { resolveAnimalImage } from "../animalImages";
import type { AnimalDetail } from "../types";
import ForestPlaceholder from "./ForestPlaceholder.vue";

const props = withDefaults(
  defineProps<{
    animal: Pick<AnimalDetail, "name" | "scientific_name">;
    variant?: number;
  }>(),
  { variant: 0 },
);

const imageUrl = ref<string | null>(null);
const loading = ref(false);
const failed = ref(false);
let requestId = 0;

watch(
  () => [props.animal.name, props.animal.scientific_name] as const,
  async () => {
    const currentRequest = ++requestId;
    imageUrl.value = null;
    failed.value = false;
    loading.value = true;
    const url = await resolveAnimalImage(props.animal);
    if (currentRequest !== requestId) return;
    imageUrl.value = url;
    loading.value = Boolean(url);
  },
  { immediate: true },
);
</script>

<template>
  <span
    class="animal-photo"
    :class="{ 'is-loading': loading, 'is-fallback': !imageUrl || failed }"
    role="img"
    :aria-label="`动物图册：${animal.name}`"
  >
    <img
      v-if="imageUrl && !failed"
      :src="imageUrl"
      alt=""
      width="1024"
      height="1024"
      loading="lazy"
      decoding="async"
      @load="loading = false"
      @error="failed = true; loading = false"
    />
    <ForestPlaceholder v-else :variant="variant" aria-hidden="true" />
  </span>
</template>
