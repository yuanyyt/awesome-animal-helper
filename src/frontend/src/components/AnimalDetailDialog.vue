<script setup lang="ts">
import { watch } from "vue";
import { ref } from "vue";

import type { AnimalDetail } from "../types";
import ForestPlaceholder from "./ForestPlaceholder.vue";

const props = defineProps<{ animal: AnimalDetail | null }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement>();

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
  (animal) => {
    if (animal) dialog.value?.showModal();
    else if (dialog.value?.open) dialog.value.close();
  },
);
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
      <article v-if="animal" class="detail-dialog__panel">
        <header class="detail-dialog__header">
          <div>
            <p>{{ animal.sites.join(" · ") }}</p>
            <h2 id="detail-title">{{ animal.name }}</h2>
            <span>{{ animal.scientific_name || "学名待补充" }}</span>
          </div>
          <button class="detail-dialog__close" type="button" aria-label="关闭动物介绍" @click="emit('close')">×</button>
        </header>

        <div class="detail-dialog__illustration">
          <ForestPlaceholder :variant="animal.name.length" />
        </div>

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

