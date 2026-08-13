<script setup lang="ts">
import { ref, watch } from "vue";

import { resolveAnimalImageSet, type AnimalImageSet } from "../animalImages";
import type { AnimalDetail } from "../types";
import ForestPlaceholder from "./ForestPlaceholder.vue";

const props = withDefaults(
  defineProps<{
    animal: Pick<AnimalDetail, "name" | "scientific_name">;
    variant?: number;
    sizes?: string;
  }>(),
  {
    variant: 0,
    sizes: "(max-width: 640px) 50vw, (max-width: 1100px) 33vw, 360px",
  },
);

const image = ref<AnimalImageSet | null>(null);
const loading = ref(false);
const failed = ref(false);
let requestId = 0;

watch(
  () => [props.animal.name, props.animal.scientific_name] as const,
  async () => {
    const currentRequest = ++requestId;
    image.value = null;
    failed.value = false;
    loading.value = true;
    const resolved = await resolveAnimalImageSet(props.animal);
    if (currentRequest !== requestId) return;
    image.value = resolved;
    loading.value = Boolean(resolved);
  },
  { immediate: true },
);

function handleImageError(): void {
  if (image.value && image.value.src !== image.value.fallback) {
    image.value = {
      fallback: image.value.fallback,
      src: image.value.fallback,
      srcset: "",
    };
    loading.value = true;
    return;
  } else {
    failed.value = true;
  }
  loading.value = false;
}
</script>

<template>
  <span
    class="animal-photo"
    :class="{ 'is-loading': loading, 'is-fallback': !image || failed }"
    role="img"
    :aria-label="`动物图册：${animal.name}`"
  >
    <img
      v-if="image && !failed"
      :src="image.src"
      :srcset="image.srcset"
      :sizes="sizes"
      alt=""
      width="1024"
      height="1024"
      loading="lazy"
      decoding="async"
      @load="loading = false"
      @error="handleImageError"
    />
    <ForestPlaceholder v-else :variant="variant" aria-hidden="true" />
  </span>
</template>
