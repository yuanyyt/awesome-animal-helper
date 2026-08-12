<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";

import {
  API_BALANCE_ERROR_CODE,
  isApiBalanceError,
  streamContinuedGuideRun,
  streamGuideMessage,
} from "../api";
import { markdownToText, renderMarkdown } from "../markdown";
import type {
  AnimalDetail,
  GuideChatResponse,
  GuideAutoRequest,
  GuideCapability,
  GuideInputField,
  MapLocationState,
  MapNamedLocation,
  MapOriginSource,
  RouteOption,
} from "../types";
import { VoiceGuideClient, type VoiceState } from "../voiceGuide";
import AnimalPhoto from "./AnimalPhoto.vue";
import AnimalSelectionChips from "./AnimalSelectionChips.vue";

const props = defineProps<{
  selectedSites: string[];
  selectedAnimals: AnimalDetail[];
  selectedSite: string;
  animals: AnimalDetail[];
  animalsLoading: boolean;
  animalsError: string;
  origin: MapNamedLocation | null;
  originSource: MapOriginSource;
  locationState: MapLocationState;
  originRevision: number;
  activeRoute: RouteOption | null;
  autoRequest: GuideAutoRequest | null;
}>();

const emit = defineEmits<{
  routes: [routes: RouteOption[]];
  routeSelect: [route: RouteOption];
  animalSelect: [animal: AnimalDetail, event: MouseEvent];
  animalRemove: [name: string];
  originPick: [];
  animalsRetry: [];
  fatalError: [code: typeof API_BALANCE_ERROR_CODE];
}>();

type TimelineItem =
  | { id: number; kind: "message"; role: "visitor" | "guide"; text: string; response?: GuideChatResponse }
  | { id: number; kind: "map" }
  | { id: number; kind: "animals" };

const capabilityOptions: { id: GuideCapability; label: string; ariaLabel: string }[] = [
  { id: "route", label: "路线规划", ariaLabel: "优先路线规划" },
  { id: "animal", label: "动物讲解", ariaLabel: "优先动物讲解" },
  { id: "service", label: "园区服务", ariaLabel: "优先园区服务" },
];

const question = ref("");
const timeline = ref<TimelineItem[]>([{ id: 1, kind: "map" }]);
const requiredInputs = ref<GuideInputField[]>([]);
const runId = ref("");
const loading = ref(false);
const error = ref("");
const voiceNotice = ref("");
const voiceState = ref<VoiceState>("disconnected");
const scrollArea = ref<HTMLElement | null>(null);
const questionInput = ref<HTMLTextAreaElement | null>(null);
const showLatestButton = ref(false);
const mapExpanded = ref(false);
const inputValues = reactive<Record<string, string | number | boolean>>({});
const sessionId = ref(window.localStorage.getItem("hongshan-guide-session") || "");
const voiceDraftReady = ref(false);
const selectedCapabilities = ref<GuideCapability[]>(["route"]);
const streamingMessageId = ref<number | null>(null);
let voiceQuestionPrefix = "";
let nextItemId = 2;
let followLatest = true;
let mapReturnFocus: HTMLElement | null = null;
let lastRouteRequest = "";
let latestRequestMessage = "";

const voiceClient = new VoiceGuideClient(
  {
    selectedSites: props.selectedSites,
    selectedAnimals: props.selectedAnimals.map((animal) => animal.name),
    origin: props.origin,
    sessionId: sessionId.value,
  },
  {
    onState: (state) => {
      voiceState.value = state;
    },
    onTranscript: (text, final) => {
      question.value = [voiceQuestionPrefix, text.trim()].filter(Boolean).join(" ");
      voiceDraftReady.value = final;
      if (final) voiceNotice.value = "";
    },
    onNotice: (message) => {
      voiceNotice.value = message;
    },
    onError: (message, code) => {
      voiceNotice.value = "";
      if (code === API_BALANCE_ERROR_CODE) {
        emit("fatalError", API_BALANCE_ERROR_CODE);
        return;
      }
      error.value = message;
    },
  },
);

watch(
  [() => props.selectedSites, () => props.selectedAnimals, () => props.origin, sessionId],
  () =>
    voiceClient.updateContext({
      selectedSites: props.selectedSites,
      selectedAnimals: props.selectedAnimals.map((animal) => animal.name),
      origin: props.origin,
      sessionId: sessionId.value,
    }),
  { deep: true },
);

watch(question, () => resizeQuestionInput());

let handledAutoRequest = 0;
watch(
  () => props.autoRequest,
  (request) => {
    if (!request || request.id === handledAutoRequest) return;
    handledAutoRequest = request.id;
    ensureCapability("route");
    void submitMessage(request.message);
  },
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

onBeforeUnmount(() => {
  voiceClient.close();
  document.body.classList.remove("has-expanded-map");
  setBackgroundInert(false);
  window.removeEventListener("keydown", handleMapEscape);
});

const voiceBusy = computed(() => !["disconnected", "idle"].includes(voiceState.value));
const voiceStatus = computed(() => {
  const labels: Record<VoiceState, string> = {
    disconnected: "点击麦克风开始语音导览",
    connecting: "正在连接森林导览员…",
    idle: "点击麦克风开始说话",
    recording: "正在听，再点一次停止并生成文字",
    transcribing: "正在把语音整理成文字…",
    speaking: "导览员正在语音讲解…",
  };
  return labels[voiceState.value];
});
const voiceActionLabel = computed(() => {
  if (voiceState.value === "recording") return "结束录音并转成文字";
  if (voiceState.value === "speaking") return "停止语音播报";
  return "开始语音输入";
});
const canSubmit = computed(
  () =>
    !loading.value &&
    !voiceBusy.value &&
    (requiredInputs.value.length > 0 || question.value.trim().length > 0),
);
const originLabel = computed(() => {
  if (props.locationState === "locating") return "正在确认你在园里的位置…";
  if (!props.origin) return "尚未设置出发位置";
  const sourceLabels: Record<MapOriginSource, string> = {
    explicit: "对话指定",
    map: "地图选定",
    geolocation: "自动定位",
    default: props.locationState === "outside" ? "园外默认入口" : "默认入口",
  };
  return `${props.origin.name} · ${sourceLabels[props.originSource]}`;
});

watch(
  () => props.originRevision,
  (revision, previous) => {
    if (!revision || revision === previous || !lastRouteRequest || loading.value) return;
    const originName = props.origin?.name ?? "新起点";
    void submitMessage(
      `起点已改为${originName}。请严格沿用我上一条路线需求重新规划，只更新路线，不要重复动物介绍。上一条需求：${lastRouteRequest}`,
      true,
      `从${originName}重新规划刚才的路线`,
    );
  },
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
      let streamedItem: Extract<TimelineItem, { kind: "message" }> | null = null;
      const response = await streamContinuedGuideRun(
        runId.value,
        sessionId.value,
        values,
        (delta) => {
          streamedItem ??= beginAssistantMessage();
          appendAssistantDelta(streamedItem, delta);
        },
      );
      handleResponse(response, streamedItem ?? beginAssistantMessage());
    } else {
      const message = question.value.trim();
      const replyWithVoice = voiceDraftReady.value;
      pushMessage("visitor", message);
      question.value = "";
      voiceDraftReady.value = false;
      await sendMessage(message, replyWithVoice);
    }
  } catch (reason) {
    if (isApiBalanceError(reason)) emit("fatalError", API_BALANCE_ERROR_CODE);
    else error.value = reason instanceof Error ? reason.message : "森林导览员暂时走开了。";
  } finally {
    streamingMessageId.value = null;
    loading.value = false;
  }
}

async function submitMessage(
  message: string,
  preserveRouteRequest = false,
  displayedMessage = message,
): Promise<void> {
  if (loading.value || !message.trim()) return;
  error.value = "";
  loading.value = true;
  pushMessage("visitor", displayedMessage.trim());
  try {
    await sendMessage(message.trim(), false, preserveRouteRequest);
  } catch (reason) {
    if (isApiBalanceError(reason)) emit("fatalError", API_BALANCE_ERROR_CODE);
    else error.value = reason instanceof Error ? reason.message : "森林导览员暂时走开了。";
  } finally {
    streamingMessageId.value = null;
    loading.value = false;
  }
}

async function sendMessage(
  message: string,
  replyWithVoice = false,
  preserveRouteRequest = false,
): Promise<void> {
  latestRequestMessage = preserveRouteRequest ? "" : message;
  let streamedItem: Extract<TimelineItem, { kind: "message" }> | null = null;
  const response = await streamGuideMessage(
    message,
    sessionId.value || null,
    props.selectedSites,
    props.selectedAnimals.map((animal) => animal.name),
    props.origin,
    selectedCapabilities.value,
    (delta) => {
      streamedItem ??= beginAssistantMessage();
      appendAssistantDelta(streamedItem, delta);
    },
  );
  handleResponse(response, streamedItem ?? beginAssistantMessage());
  if (replyWithVoice && response.assistant_message) {
    await voiceClient.speak(markdownToText(response.assistant_message));
  }
}

function handleResponse(
  response: GuideChatResponse,
  item: Extract<TimelineItem, { kind: "message" }>,
): void {
  sessionId.value = response.session_id;
  runId.value = response.run_id;
  window.localStorage.setItem("hongshan-guide-session", response.session_id);

  item.text = response.assistant_message;
  streamingMessageId.value = null;
  requiredInputs.value = response.required_inputs;
  for (const key of Object.keys(inputValues)) delete inputValues[key];
  for (const field of requiredInputs.value) inputValues[field.name] = suggestedValue(field);
  item.response = response;
  if (response.route_options.length) {
    lastRouteRequest = latestRequestMessage || lastRouteRequest;
    emit("routes", response.route_options);
    emit("routeSelect", response.route_options[1] ?? response.route_options[0]);
    showMap(true);
    moveAnimalsToEnd();
  }
  scrollToLatest();
}

function beginAssistantMessage(): Extract<TimelineItem, { kind: "message" }> {
  const item: Extract<TimelineItem, { kind: "message" }> = {
    id: nextItemId++,
    kind: "message",
    role: "guide",
    text: "",
  };
  timeline.value.push(item);
  streamingMessageId.value = item.id;
  scrollToLatest(false, true);
  return item;
}

function appendAssistantDelta(
  item: Extract<TimelineItem, { kind: "message" }>,
  delta: string,
): void {
  item.text += delta;
  scrollToLatest(false, true);
}

function chooseOrigin(): void {
  showMap(true);
  emit("originPick");
  void nextTick(() => openMap());
}

function pushMessage(role: "visitor" | "guide", text: string): void {
  if (role === "visitor") {
    followLatest = true;
    showLatestButton.value = false;
  }
  timeline.value.push({ id: nextItemId++, kind: "message", role, text });
  scrollToLatest(role === "visitor");
}

function showMap(moveToEnd = false): void {
  const index = timeline.value.findIndex((item) => item.kind === "map");
  if (index < 0) {
    timeline.value.push({ id: nextItemId++, kind: "map" });
  } else if (moveToEnd) {
    const [mapItem] = timeline.value.splice(index, 1);
    timeline.value.push(mapItem);
  }
  scrollToLatest();
}

function moveAnimalsToEnd(): void {
  const index = timeline.value.findIndex((item) => item.kind === "animals");
  if (index < 0) return;
  const [animalItem] = timeline.value.splice(index, 1);
  timeline.value.push(animalItem);
}

function suggestedValue(field: GuideInputField): string | number | boolean {
  if (field.value !== null && !Array.isArray(field.value)) return field.value;
  if (isEnergyField(field)) return "一般";
  if (isTransportField(field)) return "纯步行";
  if (isNumberField(field)) return field.name.includes("weight") ? 60 : 120;
  return "";
}

function isTransportField(field: GuideInputField): boolean {
  const text = `${field.name} ${field.description}`.toLowerCase();
  return text.includes("transport") || text.includes("出行方式") || text.includes("观光车");
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

function fieldInputId(field: GuideInputField): string {
  return `guide-input-${field.name.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function toggleCapability(capability: GuideCapability): void {
  if (selectedCapabilities.value.includes(capability)) {
    if (selectedCapabilities.value.length === 1) return;
    selectedCapabilities.value = selectedCapabilities.value.filter(
      (item) => item !== capability,
    );
    return;
  }
  selectedCapabilities.value = [...selectedCapabilities.value, capability];
}

function ensureCapability(capability: GuideCapability): void {
  if (!selectedCapabilities.value.includes(capability)) {
    selectedCapabilities.value = [...selectedCapabilities.value, capability];
  }
}

async function toggleVoice(): Promise<void> {
  if (loading.value) return;
  error.value = "";
  voiceNotice.value = "";
  try {
    if (voiceState.value === "recording") await voiceClient.stopRecording();
    else if (voiceState.value === "speaking") voiceClient.stopSpeaking();
    else {
      voiceQuestionPrefix = question.value.trim();
      voiceDraftReady.value = false;
      await voiceClient.startRecording();
    }
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

function stopVoicePlayback(): void {
  if (voiceState.value !== "speaking") return;
  voiceClient.stopSpeaking();
  voiceNotice.value = "语音播报已停止";
}

function calories(route: RouteOption): string {
  if (route.calories_kcal !== null) return `约 ${route.calories_kcal} 千卡`;
  if (route.calories_range_kcal) {
    return `约 ${route.calories_range_kcal[0]}–${route.calories_range_kcal[1]} 千卡`;
  }
  return "卡路里待估算";
}

function handleScroll(): void {
  const area = scrollArea.value;
  if (!area) return;
  followLatest = area.scrollHeight - area.scrollTop - area.clientHeight < 96;
  if (followLatest) showLatestButton.value = false;
}

function goToLatest(): void {
  followLatest = true;
  showLatestButton.value = false;
  scrollToLatest(true);
}

function openMap(): void {
  if (mapExpanded.value) return;
  mapReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  mapExpanded.value = true;
  document.body.classList.add("has-expanded-map");
  setBackgroundInert(true);
  window.addEventListener("keydown", handleMapEscape);
  void nextTick(() => {
    document.querySelector<HTMLElement>(".chat-artifact.is-map.is-expanded .chat-artifact__map-toggle")?.focus();
  });
}

function closeMap(): void {
  if (!mapExpanded.value) return;
  mapExpanded.value = false;
  document.body.classList.remove("has-expanded-map");
  setBackgroundInert(false);
  window.removeEventListener("keydown", handleMapEscape);
  void nextTick(() => mapReturnFocus?.focus());
}

function handleMapEscape(event: KeyboardEvent): void {
  if (event.key === "Escape") closeMap();
}

function setBackgroundInert(inert: boolean): void {
  for (const selector of [".site-nav", ".app-pages", ".mobile-bottom-nav"]) {
    const element = document.querySelector<HTMLElement>(selector);
    if (element) element.inert = inert;
  }
}

function scrollToLatest(force = false, streaming = false): void {
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      const area = scrollArea.value;
      if (!area) return;
      if (!force && !followLatest) {
        showLatestButton.value = true;
        return;
      }
      if (area.scrollHeight > area.clientHeight + 1) {
        area.scrollTo({
          top: area.scrollHeight,
          behavior: force || streaming ? "auto" : "smooth",
        });
        return;
      }
      const latest = area.lastElementChild;
      if (!(latest instanceof HTMLElement)) return;
      window.scrollTo({
        top: Math.max(0, latest.getBoundingClientRect().bottom + window.scrollY - window.innerHeight + 24),
        left: 0,
        behavior: streaming ? "auto" : "smooth",
      });
    });
  });
}

function resizeQuestionInput(): void {
  void nextTick(() => {
    const textarea = questionInput.value;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 96)}px`;
  });
}

function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  if (window.matchMedia("(pointer: coarse)").matches) return;
  event.preventDefault();
  void submit();
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
      </div>
    </header>

    <div
      ref="scrollArea"
      class="guide-chat__scroll"
      aria-live="polite"
      @scroll.passive="handleScroll"
    >
      <article class="chat-turn is-guide is-welcome">
        <span class="chat-turn__speaker">导览员</span>
        <div class="chat-turn__bubble">
          <p>您好，告诉我今天想怎么逛吧。</p>
        </div>
      </article>

      <template v-for="item in timeline" :key="item.id">
        <article
          v-if="item.kind === 'message'"
          class="chat-turn"
          :class="[`is-${item.role}`, { 'is-streaming': item.id === streamingMessageId }]"
          :aria-busy="item.id === streamingMessageId"
        >
          <span class="chat-turn__speaker">{{ item.role === "guide" ? "导览员" : "你" }}</span>
          <div class="chat-turn__bubble">
            <div
              v-if="item.role === 'guide'"
              class="chat-turn__markdown"
              v-html="renderMarkdown(item.text)"
            ></div>
            <p v-else>{{ item.text }}</p>
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
                <span><strong>{{ animal.name }}</strong></span>
                <em>{{ animal.sites.join(" · ") || "场馆待确认" }}</em>
              </button>
            </div>

            <div v-if="item.response?.route_options.length" class="route-options" aria-label="可选导览路线">
              <button
                v-for="route in item.response.route_options"
                :key="route.id"
                type="button"
                :class="{ 'is-active': activeRoute?.id === route.id }"
                @click="emit('routeSelect', route)"
              >
                <strong>{{ route.name }}</strong>
                <p>{{ route.description }}</p>
                <span class="route-options__meta">{{ route.sites.length }} 站 · {{ route.total_minutes }} 分钟 · 步行 {{ Math.round((route.walking_distance_meters ?? route.distance_meters) / 10) * 10 }} 米 · {{ calories(route) }}{{ route.uses_shuttle ? ` · 观光车 ${route.shuttle_fare_yuan} 元/人` : "" }}</span>
                <em v-if="route.warnings.length">{{ route.warnings.join("；") }}</em>
              </button>
            </div>
          </div>
        </article>

        <Teleport v-else-if="item.kind === 'map'" to="body" :disabled="!mapExpanded">
          <article
            class="chat-artifact is-map"
            :class="{ 'is-expanded': mapExpanded }"
            :role="mapExpanded ? 'dialog' : undefined"
            :aria-modal="mapExpanded ? 'true' : undefined"
            :aria-label="mapExpanded ? '园区地图专注视图' : undefined"
          >
            <header>
              <div>
                <span>{{ activeRoute ? `${activeRoute.name}路线图` : "园区地图" }}</span>
                <p>{{ activeRoute ? activeRoute.sites.join(" → ") : "点按场馆查看，再决定是否加入路线。" }}</p>
              </div>
              <button
                class="chat-artifact__map-toggle"
                type="button"
                :aria-label="mapExpanded ? '关闭园区地图' : '打开园区地图'"
                @click="mapExpanded ? closeMap() : openMap()"
              >
                <svg v-if="mapExpanded" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="m6 6 12 12M18 6 6 18" />
                </svg>
                <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5" />
                </svg>
                <span>{{ mapExpanded ? "关闭地图" : "打开地图" }}</span>
              </button>
            </header>
            <div class="chat-artifact__map-shell">
              <slot name="map" :expanded="mapExpanded"></slot>
            </div>
          </article>
        </Teleport>

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
              <span><strong>{{ animal.name }}</strong></span>
            </button>
          </div>
        </article>
      </template>

      <form v-if="requiredInputs.length" class="guide-chat__hitl" @submit.prevent="submit">
        <label v-for="field in requiredInputs" :key="field.name" :for="fieldInputId(field)">
          <span>{{ field.description }}</span>
          <span v-if="isEnergyField(field)" class="guide-chat__select">
            <select
              :id="fieldInputId(field)"
              v-model="inputValues[field.name]"
              :name="field.name"
            >
              <option value="轻松">轻松</option><option value="一般">一般</option><option value="充沛">充沛</option>
            </select>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
          </span>
          <span v-else-if="isTransportField(field)" class="guide-chat__select">
            <select
              :id="fieldInputId(field)"
              v-model="inputValues[field.name]"
              :name="field.name"
            >
              <option value="纯步行">纯步行</option><option value="可乘观光车">可乘观光车</option>
            </select>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
          </span>
          <input
            v-else-if="isNumberField(field)"
            :id="fieldInputId(field)"
            v-model.number="inputValues[field.name]"
            :name="field.name"
            type="number"
            min="1"
            inputmode="numeric"
            autocomplete="off"
          />
          <input
            v-else
            :id="fieldInputId(field)"
            v-model="inputValues[field.name]"
            :name="field.name"
            type="text"
            autocomplete="off"
          />
        </label>
        <button type="submit" :disabled="!canSubmit">继续规划</button>
      </form>

      <p v-if="loading && streamingMessageId === null" class="chat-thinking"><span></span><span></span><span></span>导览员正在查看资料</p>
    </div>

    <button
      v-if="showLatestButton"
      class="guide-chat__latest"
      type="button"
      @click="goToLatest"
    >
      查看新消息
      <span aria-hidden="true">↓</span>
    </button>

    <footer class="guide-chat__dock">
      <button
        class="guide-chat__origin"
        :class="{ 'is-locating': locationState === 'locating' }"
        type="button"
        :aria-label="`${originLabel}，点击在地图上更改起点`"
        @click="chooseOrigin"
      >
        <span class="guide-chat__origin-pin" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M12 21s6-5 6-11a6 6 0 1 0-12 0c0 6 6 11 6 11Z" /><circle cx="12" cy="10" r="2" /></svg>
        </span>
        <span><small>出发位置</small><strong>{{ originLabel }}</strong></span>
        <em>{{ locationState === "locating" ? "定位中" : "更改" }}</em>
      </button>
      <div class="guide-tool-chips" role="group" aria-label="选择导览偏好">
        <button
          v-for="capability in capabilityOptions"
          :key="capability.id"
          type="button"
          :class="{ 'is-active': selectedCapabilities.includes(capability.id) }"
          :aria-label="capability.ariaLabel"
          :aria-pressed="selectedCapabilities.includes(capability.id)"
          @click="toggleCapability(capability.id)"
        >
          <svg v-if="capability.id === 'route'" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="6" cy="17" r="2.25" /><circle cx="18" cy="7" r="2.25" />
            <path d="M8.3 17c5.2 0 1.4-10 7.4-10" />
          </svg>
          <svg v-else-if="capability.id === 'animal'" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8.2 11.2c-2.1-2.9-5.1-.5-3.2 2.1M15.8 11.2c2.1-2.9 5.1-.5 3.2 2.1M9.6 8.7c-.5-3.6-3.8-2.9-3.6-.3M14.4 8.7c.5-3.6 3.8-2.9 3.6-.3" />
            <path d="M12 10.5c-2.8 0-5.2 3.2-4.4 5.8.7 2.3 2.7 1.1 4.4 1.1s3.7 1.2 4.4-1.1c.8-2.6-1.6-5.8-4.4-5.8Z" />
          </svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8" /><path d="M12 11v5M12 8h.01" />
          </svg>
          <span>{{ capability.label }}</span>
        </button>
      </div>
      <AnimalSelectionChips
        :animals="selectedAnimals"
        @remove="emit('animalRemove', $event)"
      />
      <div class="guide-chat__composer">
        <label class="visually-hidden" for="guide-question">导览问题</label>
        <button
          class="guide-chat__voice"
          :class="{
            'is-recording': voiceState === 'recording',
            'is-speaking': voiceState === 'speaking',
          }"
          type="button"
          :disabled="loading || ['connecting', 'transcribing'].includes(voiceState)"
          :aria-label="voiceActionLabel"
          :title="voiceState === 'speaking' ? voiceActionLabel : voiceStatus"
          @click="toggleVoice"
        >
          <svg v-if="!['recording', 'speaking'].includes(voiceState)" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="8" y="3" width="8" height="12" rx="4" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
          </svg>
          <span v-else aria-hidden="true"></span>
        </button>
        <textarea
          id="guide-question"
          ref="questionInput"
          v-model="question"
          :readonly="requiredInputs.length > 0 || voiceBusy"
          name="guide-question"
          rows="1"
          enterkeyhint="enter"
          autocomplete="off"
          :placeholder="requiredInputs.length ? '请先补充信息…' : '问路线或动物…'"
          @input="resizeQuestionInput"
          @keydown="handleComposerKeydown"
        ></textarea>
        <button class="guide-chat__send" type="button" :disabled="!canSubmit" aria-label="发送导览问题" @click="submit">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-5 14-2.5-5.5L5 12Z" /></svg>
        </button>
      </div>
      <p
        v-if="error || voiceNotice || voiceBusy || voiceDraftReady"
        class="guide-chat__helper"
        :class="{ 'is-error': error }"
        aria-live="polite"
      >
        {{ error || voiceNotice || (voiceBusy ? voiceStatus : voiceDraftReady ? "听写完成，发送后将自动语音回复" : "") }}
      </p>
    </footer>

    <Transition name="back-to-top">
      <button
        v-if="voiceState === 'speaking'"
        class="back-to-top voice-stop-float"
        type="button"
        aria-label="停止语音播报"
        title="停止语音播报"
        @click="stopVoicePlayback"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
        </svg>
        <span>停止</span>
      </button>
    </Transition>
  </section>
</template>
