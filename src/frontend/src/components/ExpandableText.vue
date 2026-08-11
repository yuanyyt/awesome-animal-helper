<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from "vue";

const props = withDefaults(
  defineProps<{
    text: string;
    lines?: number;
  }>(),
  { lines: 4 },
);

const content = ref<HTMLElement>();
const expanded = ref(false);
const overflowing = ref(false);
const contentId = `expandable-text-${useId()}`;
let observer: ResizeObserver | undefined;

onMounted(() => {
  observer = new ResizeObserver(measure);
  if (content.value) observer.observe(content.value);
  void nextTick(measure);
});

onBeforeUnmount(() => observer?.disconnect());

watch(
  () => props.text,
  async () => {
    expanded.value = false;
    await nextTick();
    measure();
  },
);

function measure(): void {
  const element = content.value;
  if (!element || expanded.value) return;
  overflowing.value = element.scrollHeight > element.clientHeight + 1;
}

function toggle(): void {
  expanded.value = !expanded.value;
  if (!expanded.value) void nextTick(measure);
}
</script>

<template>
  <div class="expandable-text">
    <p
      :id="contentId"
      ref="content"
      class="expandable-text__content"
      :class="{ 'is-expanded': expanded }"
      :style="{ '--expandable-lines': lines }"
    >
      {{ text }}
    </p>
    <button
      v-if="overflowing"
      class="expandable-text__toggle"
      type="button"
      :aria-controls="contentId"
      :aria-expanded="expanded"
      @click="toggle"
    >
      {{ expanded ? "收起" : "展开" }}
      <span aria-hidden="true">{{ expanded ? "↑" : "↓" }}</span>
    </button>
  </div>
</template>
