import type { MapNamedLocation } from "./types";

export type VoiceState =
  | "disconnected"
  | "connecting"
  | "idle"
  | "recording"
  | "transcribing"
  | "speaking";

interface VoiceMapContext {
  selectedSites: string[];
  selectedAnimals: string[];
  origin: MapNamedLocation | null;
  sessionId: string;
}

interface VoiceHandlers {
  onState: (state: VoiceState) => void;
  onTranscript: (text: string, final: boolean) => void;
  onSpeechEnd?: () => void;
  onNotice?: (message: string) => void;
  onError: (message: string) => void;
}

interface ServerEvent {
  type: string;
  state?: VoiceState;
  text?: string;
  message?: string;
}

const READY_TIMEOUT_MS = 15_000;
const SILENCE_TIMEOUT_MS = 15_000;
const MAX_RECORDING_MS = 270_000;
const OUTPUT_SAMPLE_RATE = 24_000;

export class VoiceGuideClient {
  private socket: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private recorder: AudioWorkletNode | null = null;
  private state: VoiceState = "disconnected";
  private transcriptDraft = "";
  private playbackTime = 0;
  private playbackSources = new Set<AudioBufferSourceNode>();
  private workletLoaded = false;
  private readyPromise: Promise<void> | null = null;
  private resolveReady: (() => void) | null = null;
  private rejectReady: ((reason: Error) => void) | null = null;
  private silenceTimer: number | null = null;
  private recordingLimitTimer: number | null = null;
  private detectedSpeech = false;
  private finalizingRecording = false;
  private closed = false;
  private context: VoiceMapContext;

  constructor(context: VoiceMapContext, private readonly handlers: VoiceHandlers) {
    this.context = context;
  }

  updateContext(context: VoiceMapContext): void {
    this.context = context;
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.sendJson({ type: "context.update", map_context: this.mapContextPayload() });
      this.sendJson({ type: "session.update", session_id: this.context.sessionId });
    }
  }

  async startRecording(): Promise<void> {
    if (this.recorder) return;
    this.closed = false;
    this.transcriptDraft = "";
    await this.ensureConnected();
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持实时语音，请使用最新版浏览器");
    }
    this.stopPlayback();
    const audioContext = await this.ensureAudioContext();
    if (!this.workletLoaded) {
      await audioContext.audioWorklet.addModule("/audio/pcm-recorder-worklet.js");
      this.workletLoaded = true;
    }
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const source = audioContext.createMediaStreamSource(this.mediaStream);
    const recorder = new AudioWorkletNode(audioContext, "pcm-recorder");
    const silence = audioContext.createGain();
    silence.gain.value = 0;
    source.connect(recorder).connect(silence).connect(audioContext.destination);
    recorder.port.onmessage = (event: MessageEvent<{ type: string; buffer?: ArrayBuffer }>) => {
      if (event.data.type === "voice-activity") {
        this.detectedSpeech = true;
        this.armSilenceTimer();
      } else if (
        event.data.type === "pcm" &&
        event.data.buffer &&
        this.socket?.readyState === WebSocket.OPEN
      ) {
        this.socket.send(event.data.buffer);
      }
    };
    this.recorder = recorder;
    this.detectedSpeech = false;
    this.armRecordingTimers();
    this.setState("recording");
  }

  async stopRecording(): Promise<void> {
    await this.finishRecording("manual");
  }

  private async finishRecording(reason: "manual" | "silence" | "limit"): Promise<void> {
    const recorder = this.recorder;
    if (!recorder || this.finalizingRecording) return;
    this.finalizingRecording = true;
    try {
      const shouldCommit = reason !== "silence" || this.detectedSpeech;
      if (!shouldCommit) {
        this.releaseRecorder();
        this.transcriptDraft = "";
        this.sendJson({ type: "cancel" });
        this.handlers.onNotice?.("连续15秒未检测到语音，录音已停止");
        this.setState("idle");
        return;
      }
      await new Promise<void>((resolve) => {
        let settled = false;
        const listener = (event: MessageEvent<{ type: string }>) => {
          if (event.data.type !== "flushed" || settled) return;
          settled = true;
          recorder.port.removeEventListener("message", listener);
          resolve();
        };
        recorder.port.addEventListener("message", listener);
        recorder.port.start();
        recorder.port.postMessage({ type: "flush" });
        window.setTimeout(() => {
          if (settled) return;
          settled = true;
          recorder.port.removeEventListener("message", listener);
          resolve();
        }, 500);
      });
      if (this.recorder !== recorder) return;
      this.releaseRecorder();
      this.sendJson({ type: "commit" });
      if (reason === "limit") {
        this.handlers.onNotice?.("单次录音已达4分30秒，正在转成文字");
      }
      this.setState("transcribing");
    } finally {
      this.finalizingRecording = false;
    }
  }

  cancel(): void {
    this.releaseRecorder();
    if (this.socket?.readyState === WebSocket.OPEN) this.sendJson({ type: "cancel" });
    this.transcriptDraft = "";
    this.setState("idle");
  }

  async speak(text: string): Promise<void> {
    if (!text.trim()) return;
    await this.ensureConnected();
    await this.ensureAudioContext();
    this.stopPlayback();
    this.sendJson({ type: "speak", text: text.trim() });
    this.setState("speaking");
  }

  close(): void {
    this.closed = true;
    this.releaseRecorder();
    this.stopPlayback();
    this.socket?.close(1000, "component unmounted");
    this.socket = null;
    void this.audioContext?.close();
    this.audioContext = null;
    this.workletLoaded = false;
  }

  private async ensureConnected(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN && this.readyPromise) {
      await this.readyPromise;
      return;
    }
    let lastError: unknown;
    for (const delay of [0, 500, 1_000]) {
      if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay));
      try {
        await this.connect();
        return;
      } catch (error) {
        lastError = error;
        this.socket?.close();
        this.socket = null;
      }
    }
    throw lastError instanceof Error ? lastError : new Error("无法连接实时语音服务");
  }

  private async connect(): Promise<void> {
    this.setState("connecting");
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/api/guide/voice`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.readyPromise = new Promise<void>((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });
    socket.onopen = () => {
      this.sendJson({
        type: "configure",
        session_id: this.context.sessionId || null,
        map_context: this.mapContextPayload(),
      });
    };
    socket.onmessage = (event) => this.handleMessage(event);
    socket.onerror = () => this.handlers.onError("语音连接遇到网络问题");
    socket.onclose = () => {
      this.releaseRecorder();
      this.rejectReady?.(new Error("语音连接已断开"));
      this.socket = null;
      this.readyPromise = null;
      this.resolveReady = null;
      this.rejectReady = null;
      if (!this.closed) this.setState("disconnected");
    };
    await Promise.race([
      this.readyPromise,
      new Promise<never>((_, reject) =>
        window.setTimeout(() => reject(new Error("语音服务连接超时")), READY_TIMEOUT_MS),
      ),
    ]);
  }

  private handleMessage(event: MessageEvent<string | ArrayBuffer>): void {
    if (event.data instanceof ArrayBuffer) {
      this.enqueuePlayback(event.data);
      return;
    }
    const payload = JSON.parse(event.data) as ServerEvent;
    if (payload.type === "ready") {
      this.resolveReady?.();
      this.resolveReady = null;
      this.rejectReady = null;
    } else if (payload.type === "state" && payload.state) {
      if (payload.state !== "recording" && this.recorder) this.releaseRecorder();
      this.setState(payload.state);
    } else if (payload.type === "notice" && payload.message) {
      this.handlers.onNotice?.(payload.message);
    } else if (payload.type === "transcript.user.delta" && payload.text) {
      this.transcriptDraft += payload.text;
      this.handlers.onTranscript(this.transcriptDraft, false);
    } else if (payload.type === "transcript.user.done") {
      const text = payload.text?.trim() || this.transcriptDraft.trim();
      if (text) this.handlers.onTranscript(text, true);
      this.transcriptDraft = text;
    } else if (payload.type === "speech.done") {
      this.handlers.onSpeechEnd?.();
    } else if (payload.type === "error" || payload.type === "tool.error") {
      this.handlers.onError(payload.message || "实时语音请求失败");
    }
  }

  private releaseRecorder(): void {
    this.clearRecordingTimers();
    this.recorder?.disconnect();
    this.recorder = null;
    for (const track of this.mediaStream?.getTracks() || []) track.stop();
    this.mediaStream = null;
    this.detectedSpeech = false;
  }

  private armRecordingTimers(): void {
    this.clearRecordingTimers();
    this.armSilenceTimer();
    this.recordingLimitTimer = window.setTimeout(() => {
      void this.finishRecording("limit").catch((error: unknown) => {
        this.handlers.onError(error instanceof Error ? error.message : "无法结束语音录音");
      });
    }, MAX_RECORDING_MS);
  }

  private armSilenceTimer(): void {
    if (this.silenceTimer !== null) window.clearTimeout(this.silenceTimer);
    this.silenceTimer = window.setTimeout(() => {
      void this.finishRecording("silence").catch((error: unknown) => {
        this.handlers.onError(error instanceof Error ? error.message : "无法结束语音录音");
      });
    }, SILENCE_TIMEOUT_MS);
  }

  private clearRecordingTimers(): void {
    if (this.silenceTimer !== null) window.clearTimeout(this.silenceTimer);
    if (this.recordingLimitTimer !== null) window.clearTimeout(this.recordingLimitTimer);
    this.silenceTimer = null;
    this.recordingLimitTimer = null;
  }

  private async ensureAudioContext(): Promise<AudioContext> {
    if (!window.AudioContext) throw new Error("当前浏览器不支持语音播放");
    this.audioContext ??= new AudioContext();
    await this.audioContext.resume();
    return this.audioContext;
  }

  private enqueuePlayback(pcm: ArrayBuffer): void {
    const context = this.audioContext;
    if (!context || !pcm.byteLength) return;
    const samples = new Float32Array(pcm.byteLength / 2);
    const view = new DataView(pcm);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 32768;
    }
    const buffer = context.createBuffer(1, samples.length, OUTPUT_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.onended = () => this.playbackSources.delete(source);
    this.playbackSources.add(source);
    this.playbackTime = Math.max(context.currentTime + 0.03, this.playbackTime);
    source.start(this.playbackTime);
    this.playbackTime += buffer.duration;
  }

  private stopPlayback(): void {
    for (const source of this.playbackSources) source.stop();
    this.playbackSources.clear();
    this.playbackTime = 0;
  }

  private mapContextPayload(): object {
    return {
      selected_sites: this.context.selectedSites,
      selected_animals: this.context.selectedAnimals,
      origin: this.context.origin,
    };
  }

  private sendJson(payload: object): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(payload));
  }

  private setState(state: VoiceState): void {
    this.state = state;
    this.handlers.onState(state);
  }
}
