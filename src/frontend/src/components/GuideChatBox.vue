<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";

import { continueGuideRun, sendGuideMessage } from "../api";
import type {
  GuideChatResponse,
  GuideInputField,
  MapNamedLocation,
  RouteOption,
} from "../types";
import { VoiceGuideClient, type VoiceState } from "../voiceGuide";

const props = defineProps<{
  selectedSites: string[];
  origin: MapNamedLocation | null;
  activeRouteId: string;
}>();

const emit = defineEmits<{
  routes: [routes: RouteOption[]];
  routeSelect: [route: RouteOption];
}>();

interface ChatMessage {
  role: "visitor" | "guide";
  text: string;
}

const question = ref("");
const messages = ref<ChatMessage[]>([]);
const requiredInputs = ref<GuideInputField[]>([]);
const routes = ref<RouteOption[]>([]);
const runId = ref("");
const loading = ref(false);
const error = ref("");
const voiceState = ref<VoiceState>("disconnected");
const voiceMessageIndex = ref<number | null>(null);
const inputValues = reactive<Record<string, string | number | boolean>>({});
const sessionId = ref(window.localStorage.getItem("hongshan-guide-session") || "");

const voiceClient = new VoiceGuideClient(
  { selectedSites: props.selectedSites, origin: props.origin },
  {
    onState: (state) => {
      voiceState.value = state;
    },
    onUserTranscript: (text) => messages.value.push({ role: "visitor", text }),
    onAssistantDelta: appendVoiceAnswer,
    onAssistantDone: finishVoiceAnswer,
    onRoutes: handleVoiceRoutes,
    onError: (message) => {
      error.value = message;
    },
  },
);

watch(
  [() => props.selectedSites, () => props.origin],
  () => voiceClient.updateContext({ selectedSites: props.selectedSites, origin: props.origin }),
  { deep: true },
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
    speaking: "导览员正在回答，再点麦克风可以打断",
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
      const response = await continueGuideRun(runId.value, sessionId.value, values);
      messages.value.push({ role: "visitor", text: summarizeInputs() });
      handleResponse(response);
    } else {
      const message = question.value.trim();
      messages.value.push({ role: "visitor", text: message });
      question.value = "";
      handleResponse(
        await sendGuideMessage(
          message,
          sessionId.value || null,
          props.selectedSites,
          props.origin,
        ),
      );
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "森林导览员暂时走开了。";
  } finally {
    loading.value = false;
  }
}

function handleResponse(response: GuideChatResponse): void {
  sessionId.value = response.session_id;
  runId.value = response.run_id;
  window.localStorage.setItem("hongshan-guide-session", response.session_id);
  requiredInputs.value = response.required_inputs;
  for (const key of Object.keys(inputValues)) delete inputValues[key];
  for (const field of requiredInputs.value) {
    inputValues[field.name] = suggestedValue(field);
  }
  messages.value.push({ role: "guide", text: response.assistant_message });
  routes.value = response.route_options;
  if (routes.value.length) {
    emit("routes", routes.value);
    emit("routeSelect", routes.value[1] ?? routes.value[0]);
  }
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
    if (voiceState.value === "recording") {
      await voiceClient.stopRecording();
    } else {
      voiceMessageIndex.value = null;
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

function appendVoiceAnswer(delta: string): void {
  if (voiceMessageIndex.value === null) {
    messages.value.push({ role: "guide", text: "" });
    voiceMessageIndex.value = messages.value.length - 1;
  }
  messages.value[voiceMessageIndex.value].text += delta;
}

function finishVoiceAnswer(text: string): void {
  if (voiceMessageIndex.value === null && text) {
    messages.value.push({ role: "guide", text });
  } else if (voiceMessageIndex.value !== null && text) {
    messages.value[voiceMessageIndex.value].text = text;
  }
  voiceMessageIndex.value = null;
}

function handleVoiceRoutes(options: RouteOption[]): void {
  routes.value = options;
  if (!options.length) return;
  emit("routes", options);
  emit("routeSelect", options[1] ?? options[0]);
}

function calories(route: RouteOption): string {
  if (route.calories_kcal !== null) return `约 ${route.calories_kcal} 千卡`;
  if (route.calories_range_kcal) {
    return `约 ${route.calories_range_kcal[0]}–${route.calories_range_kcal[1]} 千卡`;
  }
  return "卡路里待估算";
}
</script>

<template>
  <aside class="guide-chat" aria-labelledby="guide-chat-title">
    <div class="guide-chat__intro">
      <span aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M5 18c1-7 5-11 14-12-1 9-5 13-12 13" /><path d="M7 19c3-4 6-7 11-10" /></svg>
      </span>
      <div>
        <h3 id="guide-chat-title">问问森林导览员</h3>
        <p>{{ selectedSites.length ? `地图已选 ${selectedSites.length} 个场馆` : "可以先点地图，也可以直接告诉我想看什么" }}</p>
      </div>
      <strong>AGNO · VOICE</strong>
    </div>

    <div class="guide-chat__scroll">
      <div v-if="messages.length" class="guide-chat__messages" aria-live="polite">
        <p v-for="(message, index) in messages" :key="index" :class="`is-${message.role}`">
          <span>{{ message.role === "guide" ? "导览员" : "你" }}</span>{{ message.text }}
        </p>
      </div>

      <div v-if="requiredInputs.length" class="guide-chat__hitl">
        <p class="guide-chat__hitl-title">补充这些信息，就可以继续规划</p>
        <label v-for="field in requiredInputs" :key="field.name">
          <span>{{ field.description }}</span>
          <select v-if="isEnergyField(field)" v-model="inputValues[field.name]">
            <option value="轻松">轻松</option><option value="一般">一般</option><option value="充沛">充沛</option>
          </select>
          <input
            v-else-if="isNumberField(field)"
            v-model.number="inputValues[field.name]"
            type="number"
            min="1"
          />
          <input v-else v-model="inputValues[field.name]" type="text" />
        </label>
      </div>

      <div v-else-if="!messages.length" class="guide-chat__prompts" aria-label="导览问题示例">
        <button type="button" @click="choosePrompt('我有两个小时，体力一般，帮我规划路线')">两小时均衡路线</button>
        <button type="button" @click="choosePrompt('带孩子轻松逛，想看大熊猫和考拉')">亲子轻松路线</button>
        <button type="button" @click="choosePrompt('我想尽量多看动物，体力充沛')">尽兴探索路线</button>
      </div>

      <div v-if="routes.length" class="route-options" aria-label="可选导览路线">
        <button
          v-for="route in routes"
          :key="route.id"
          type="button"
          :class="{ 'is-active': activeRouteId === route.id }"
          @click="emit('routeSelect', route)"
        >
          <span class="route-options__eyebrow">{{ route.sites.length }} 站 · {{ Math.round(route.distance_meters / 10) * 10 }} 米</span>
          <strong>{{ route.name }}</strong>
          <p>{{ route.description }}</p>
          <small>{{ route.total_minutes }} 分钟 · {{ calories(route) }}</small>
          <em v-if="route.has_stairs">含阶梯路段</em>
        </button>
      </div>
    </div>

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
        :placeholder="requiredInputs.length ? '请先填写上面的信息…' : '例如：我有两小时，带着孩子，想先看大熊猫…'"
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <button class="guide-chat__send" type="button" :disabled="!canSubmit" aria-label="发送导览问题" @click="submit">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-5 14-2.5-5.5L5 12Z" /></svg>
      </button>
    </div>
    <p class="guide-chat__helper" :class="{ 'is-error': error }">
      {{ error || (loading ? "导览员正在查看地图和路况…" : voiceBusy ? voiceStatus : "可打字或点击麦克风；距离与时间来自高德地图") }}
    </p>

  </aside>
</template>
