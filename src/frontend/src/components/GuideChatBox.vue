<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";

import { continueGuideRun, sendGuideMessage } from "../api";
import type {
  AnimalDetail,
  GuideChatResponse,
  GuideInputField,
  MapNamedLocation,
  RouteOption,
} from "../types";
import { VoiceGuideClient, type VoiceState } from "../voiceGuide";
import AnimalPhoto from "./AnimalPhoto.vue";

const props = defineProps<{
  selectedSites: string[];
  selectedSite: string;
  animals: AnimalDetail[];
  animalsLoading: boolean;
  animalsError: string;
  origin: MapNamedLocation | null;
  activeRouteId: string;
}>();

const emit = defineEmits<{
  routes: [routes: RouteOption[]];
  routeSelect: [route: RouteOption];
  animalSelect: [animal: AnimalDetail, event: MouseEvent];
  animalsRetry: [];
}>();

type TimelineItem =
  | { id: number; kind: "message"; role: "visitor" | "guide"; text: string; response?: GuideChatResponse }
  | { id: number; kind: "map" }
  | { id: number; kind: "animals" };

const question = ref("");
const timeline = ref<TimelineItem[]>([]);
const requiredInputs = ref<GuideInputField[]>([]);
const runId = ref("");
const loading = ref(false);
const error = ref("");
const voiceState = ref<VoiceState>("disconnected");
const scrollArea = ref<HTMLElement | null>(null);
const inputValues = reactive<Record<string, string | number | boolean>>({});
const sessionId = ref(window.localStorage.getItem("hongshan-guide-session") || "");
let nextItemId = 1;

const voiceClient = new VoiceGuideClient(
  {
    selectedSites: props.selectedSites,
    origin: props.origin,
    sessionId: sessionId.value,
  },
  {
    onState: (state) => {
      voiceState.value = state;
    },
    onUserTranscript: (text) => pushMessage("visitor", text),
    onGuideResponse: handleResponse,
    onError: (message) => {
      error.value = message;
    },
  },
);

watch(
  [() => props.selectedSites, () => props.origin, sessionId],
  () =>
    voiceClient.updateContext({
      selectedSites: props.selectedSites,
      origin: props.origin,
      sessionId: sessionId.value,
    }),
  { deep: true },
);

watch(
  () => props.selectedSite,
  (site) => {
    if (!site) return;
    showMap();
    timeline.value = timeline.value.filter((item) => item.kind !== "animals");
    timeline.value.push({ id: nextItemId++, kind: "animals" });
    scrollToLatest();
  },
);

onBeforeUnmount(() => voiceClient.close());

const voiceBusy = computed(() => !["disconnected", "idle"].includes(voiceState.value));
const voiceStatus = computed(() => {
  const labels: Record<VoiceState, string> = {
    disconnected: "点击麦克风开始语音导览",
    connecting: "正在连接森林导览员…",
    idle: "点击麦克风开始说话",
    recording: "正在听，再点一次就发送",
    thinking: "导览员正在查资料和地图…",
    speaking: "导览员正在朗读，再点麦克风可以打断",
  };
  return labels[voiceState.value];
});
const canSubmit = computed(
  () =>
    !loading.value &&
    !voiceBusy.value &&
    (requiredInputs.value.length > 0 || question.value.trim().length > 0),
);

async function submit(): Promise<void> {
  if (!canSubmit.value) return;
  error.value = "";
  loading.value = true;
  try {
    if (requiredInputs.value.length) {
      const values = Object.fromEntries(
        requiredInputs.value.map((field) => [field.name, inputValues[field.name] ?? ""]),
      );
      pushMessage("visitor", summarizeInputs());
      handleResponse(await continueGuideRun(runId.value, sessionId.value, values));
    } else {
      const message = question.value.trim();
      pushMessage("visitor", message);
      question.value = "";
      handleResponse(
        await sendGuideMessage(message, sessionId.value || null, props.selectedSites, props.origin),
      );
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "森林导览员暂时走开了。";
  } finally {
    loading.value = false;
    scrollToLatest();
  }
}

function handleResponse(response: GuideChatResponse): void {
  sessionId.value = response.session_id;
  runId.value = response.run_id;
  window.localStorage.setItem("hongshan-guide-session", response.session_id);
  requiredInputs.value = response.required_inputs;
  for (const key of Object.keys(inputValues)) delete inputValues[key];
  for (const field of requiredInputs.value) inputValues[field.name] = suggestedValue(field);

  timeline.value.push({
    id: nextItemId++,
    kind: "message",
    role: "guide",
    text: response.assistant_message,
    response,
  });
  if (response.route_options.length) {
    emit("routes", response.route_options);
    emit("routeSelect", response.route_options[1] ?? response.route_options[0]);
    showMap();
  }
  scrollToLatest();
}

function pushMessage(role: "visitor" | "guide", text: string): void {
  timeline.value.push({ id: nextItemId++, kind: "message", role, text });
  scrollToLatest();
}

function showMap(): void {
  if (timeline.value.some((item) => item.kind === "map")) return;
  timeline.value.push({ id: nextItemId++, kind: "map" });
  scrollToLatest();
}

function suggestedValue(field: GuideInputField): string | number | boolean {
  if (field.value !== null && !Array.isArray(field.value)) return field.value;
  if (isEnergyField(field)) return "一般";
  if (isNumberField(field)) return field.name.includes("weight") ? 60 : 120;
  return "";
}

function isEnergyField(field: GuideInputField): boolean {
  const text = `${field.name} ${field.description}`.toLowerCase();
  return text.includes("energy") || text.includes("体力");
}

function isNumberField(field: GuideInputField): boolean {
  const type = field.field_type.toLowerCase();
  return type.includes("int") || type.includes("float");
}

function summarizeInputs(): string {
  return requiredInputs.value
    .map((field) => `${field.description}：${String(inputValues[field.name] ?? "")}`)
    .join("；");
}

function choosePrompt(prompt: string): void {
  question.value = prompt;
}

async function toggleVoice(): Promise<void> {
  if (loading.value) return;
  error.value = "";
  try {
    if (voiceState.value === "recording") await voiceClient.stopRecording();
    else await voiceClient.startRecording();
  } catch (reason) {
    error.value =
      reason instanceof DOMException && reason.name === "NotAllowedError"
        ? "请允许浏览器使用麦克风后再试"
        : reason instanceof Error
          ? reason.message
          : "无法开始语音导览";
    voiceState.value = "disconnected";
  }
}

function calories(route: RouteOption): string {
  if (route.calories_kcal !== null) return `约 ${route.calories_kcal} 千卡`;
  if (route.calories_range_kcal) {
    return `约 ${route.calories_range_kcal[0]}–${route.calories_range_kcal[1]} 千卡`;
  }
  return "卡路里待估算";
}

function scrollToLatest(): void {
  void nextTick(() => scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight, behavior: "smooth" }));
}
</script>

<template>
  <section class="guide-chat" aria-labelledby="guide-chat-title">
    <header class="guide-chat__intro">
      <span aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M5 18c1-7 5-11 14-12-1 9-5 13-12 13" /><path d="M7 19c3-4 6-7 11-10" /></svg>
      </span>
      <div>
        <h2 id="guide-chat-title">问问森林导览员</h2>
        <p>{{ selectedSites.length ? `已选 ${selectedSites.length} 个场馆` : "路线和动物知识，都可以从一句话开始" }}</p>
      </div>
      <strong>AGNO · VOICE</strong>
    </header>

    <div ref="scrollArea" class="guide-chat__scroll" aria-live="polite">
      <article class="chat-turn is-guide is-welcome">
        <span class="chat-turn__speaker">导览员</span>
        <div class="chat-turn__bubble">
          <p>您好，我可以陪您规划园内路线，也可以讲讲动物邻居的故事。</p>
          <div class="guide-chat__prompts" aria-label="导览快捷操作">
            <button type="button" @click="showMap">打开园区地图</button>
            <button type="button" @click="choosePrompt('带孩子轻松逛，想看大熊猫和考拉')">亲子轻松路线</button>
            <button type="button" @click="choosePrompt('给我介绍一下大熊猫的生活习性')">认识大熊猫</button>
          </div>
        </div>
      </article>

      <template v-for="item in timeline" :key="item.id">
        <article v-if="item.kind === 'message'" class="chat-turn" :class="`is-${item.role}`">
          <span class="chat-turn__speaker">{{ item.role === "guide" ? "导览员" : "你" }}</span>
          <div class="chat-turn__bubble">
            <p>{{ item.text }}</p>
            <p v-if="item.response?.unresolved_terms.length" class="chat-turn__warning">
              暂无可靠地图点位：{{ item.response.unresolved_terms.join("、") }}
            </p>

            <div v-if="item.response?.knowledge_items.length" class="knowledge-cards">
              <button
                v-for="(animal, index) in item.response.knowledge_items"
                :key="animal.name"
                type="button"
                @click="emit('animalSelect', animal, $event)"
              >
                <AnimalPhoto :animal="animal" :variant="index" />
                <span><strong>{{ animal.name }}</strong><small>{{ animal.scientific_name || "学名待补充" }}</small></span>
                <em>{{ animal.sites.join(" · ") || "场馆待确认" }}</em>
              </button>
            </div>

            <div v-if="item.response?.route_options.length" class="route-options" aria-label="可选导览路线">
              <button
                v-for="route in item.response.route_options"
                :key="route.id"
                type="button"
                :class="{ 'is-active': activeRouteId === route.id }"
                @click="emit('routeSelect', route)"
              >
                <span class="route-options__eyebrow">{{ route.sites.length }} 站 · {{ Math.round(route.distance_meters / 10) * 10 }} 米</span>
                <strong>{{ route.name }}</strong>
                <p>{{ route.description }}</p>
                <small>{{ route.total_minutes }} 分钟 · {{ calories(route) }}</small>
                <em v-if="route.warnings.length">{{ route.warnings.join("；") }}</em>
              </button>
            </div>
          </div>
        </article>

        <article v-else-if="item.kind === 'map'" class="chat-artifact is-map">
          <header><span>园区地图</span><p>点按场馆，加入今天的路线。</p></header>
          <slot name="map"></slot>
        </article>

        <article v-else class="chat-artifact is-animals">
          <header><span>{{ selectedSite }}</span><p>住在这座场馆的动物邻居</p></header>
          <div v-if="animalsLoading" class="chat-animal-grid" aria-label="正在加载场馆动物">
            <div v-for="index in 4" :key="index" class="chat-animal-skeleton"></div>
          </div>
          <div v-else-if="animalsError" class="chat-artifact__empty is-error">
            <p>{{ animalsError }}</p><button type="button" @click="emit('animalsRetry')">重新打开名册</button>
          </div>
          <div v-else-if="!animals.length" class="chat-artifact__empty"><p>这座场馆暂时没有匹配的动物资料。</p></div>
          <div v-else class="chat-animal-grid">
            <button
              v-for="(animal, index) in animals"
              :key="animal.name"
              type="button"
              @click="emit('animalSelect', animal, $event)"
            >
              <AnimalPhoto :animal="animal" :variant="index" />
              <span><strong>{{ animal.name }}</strong><small>{{ animal.scientific_name || "学名待补充" }}</small></span>
            </button>
          </div>
        </article>
      </template>

      <form v-if="requiredInputs.length" class="guide-chat__hitl" @submit.prevent="submit">
        <p class="guide-chat__hitl-title">补充这些信息，就可以继续规划</p>
        <label v-for="field in requiredInputs" :key="field.name">
          <span>{{ field.description }}</span>
          <select v-if="isEnergyField(field)" v-model="inputValues[field.name]">
            <option value="轻松">轻松</option><option value="一般">一般</option><option value="充沛">充沛</option>
          </select>
          <input v-else-if="isNumberField(field)" v-model.number="inputValues[field.name]" type="number" min="1" />
          <input v-else v-model="inputValues[field.name]" type="text" />
        </label>
        <button type="submit" :disabled="!canSubmit">继续规划</button>
      </form>

      <p v-if="loading" class="chat-thinking"><span></span><span></span><span></span>导览员正在查看资料</p>
    </div>

    <footer class="guide-chat__dock">
      <div class="guide-chat__composer">
        <label class="visually-hidden" for="guide-question">导览问题</label>
        <button
          class="guide-chat__voice"
          :class="{ 'is-recording': voiceState === 'recording' }"
          type="button"
          :disabled="loading || voiceState === 'connecting'"
          :aria-label="voiceState === 'recording' ? '结束录音并发送' : '开始语音导览'"
          :title="voiceStatus"
          @click="toggleVoice"
        >
          <svg v-if="voiceState !== 'recording'" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="8" y="3" width="8" height="12" rx="4" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
          </svg>
          <span v-else aria-hidden="true"></span>
        </button>
        <textarea
          id="guide-question"
          v-model="question"
          :readonly="requiredInputs.length > 0 || voiceBusy"
          rows="2"
          :placeholder="requiredInputs.length ? '请先填写上面的信息…' : '问路线，或深入了解一种动物…'"
          @keydown.enter.exact.prevent="submit"
        ></textarea>
        <button class="guide-chat__send" type="button" :disabled="!canSubmit" aria-label="发送导览问题" @click="submit">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-5 14-2.5-5.5L5 12Z" /></svg>
        </button>
      </div>
      <p class="guide-chat__helper" :class="{ 'is-error': error }">
        {{ error || (voiceBusy ? voiceStatus : "语音和文字会记录在同一段导览对话中") }}
      </p>
    </footer>
  </section>
</template>
