import type { MapNamedLocation } from "./types";

export type VoiceState =
  | "disconnected"
  | "connecting"
  | "idle"
  | "recording"
  | "transcribing";

interface VoiceMapContext {
  selectedSites: string[];
  selectedAnimals: string[];
  origin: MapNamedLocation | null;
  sessionId: string;
}

interface VoiceHandlers {
  onState: (state: VoiceState) => void;
  onTranscript: (text: string, final: boolean) => void;
  onError: (message: string) => void;
}

interface ServerEvent {
  type: string;
  state?: VoiceState;
  text?: string;
  message?: string;
}

const READY_TIMEOUT_MS = 15_000;

export class VoiceGuideClient {
  private socket: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private recorder: AudioWorkletNode | null = null;
  private state: VoiceState = "disconnected";
  private transcriptDraft = "";
  private workletLoaded = false;
  private readyPromise: Promise<void> | null = null;
  private resolveReady: (() => void) | null = null;
  private rejectReady: ((reason: Error) => void) | null = null;
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
    this.closed = false;
    this.transcriptDraft = "";
    await this.ensureConnected();
    const AudioContextClass = window.AudioContext;
    if (!AudioContextClass || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持实时语音，请使用最新版浏览器");
    }
    this.audioContext ??= new AudioContextClass();
    await this.audioContext.resume();
    if (!this.workletLoaded) {
      await this.audioContext.audioWorklet.addModule("/audio/pcm-recorder-worklet.js");
      this.workletLoaded = true;
    }
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const source = this.audioContext.createMediaStreamSource(this.mediaStream);
    const recorder = new AudioWorkletNode(this.audioContext, "pcm-recorder");
    const silence = this.audioContext.createGain();
    silence.gain.value = 0;
    source.connect(recorder).connect(silence).connect(this.audioContext.destination);
    recorder.port.onmessage = (event: MessageEvent<{ type: string; buffer?: ArrayBuffer }>) => {
      if (event.data.type === "pcm" && event.data.buffer && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(event.data.buffer);
      }
    };
    this.recorder = recorder;
    this.setState("recording");
  }

  async stopRecording(): Promise<void> {
    const recorder = this.recorder;
    if (!recorder) return;
    await new Promise<void>((resolve) => {
      const listener = (event: MessageEvent<{ type: string }>) => {
        if (event.data.type !== "flushed") return;
        recorder.port.removeEventListener("message", listener);
        resolve();
      };
      recorder.port.addEventListener("message", listener);
      recorder.port.start();
      recorder.port.postMessage({ type: "flush" });
      window.setTimeout(resolve, 500);
    });
    this.releaseRecorder();
    this.sendJson({ type: "commit" });
    this.setState("transcribing");
  }

  cancel(): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.sendJson({ type: "cancel" });
    this.transcriptDraft = "";
    this.setState("idle");
  }

  close(): void {
    this.closed = true;
    this.releaseRecorder();
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
    if (event.data instanceof ArrayBuffer) return;
    const payload = JSON.parse(event.data) as ServerEvent;
    if (payload.type === "ready") {
      this.resolveReady?.();
      this.resolveReady = null;
      this.rejectReady = null;
    } else if (payload.type === "state" && payload.state) {
      this.setState(payload.state);
    } else if (payload.type === "transcript.user.delta" && payload.text) {
      this.transcriptDraft += payload.text;
      this.handlers.onTranscript(this.transcriptDraft, false);
    } else if (payload.type === "transcript.user.done") {
      const text = payload.text?.trim() || this.transcriptDraft.trim();
      if (text) this.handlers.onTranscript(text, true);
      this.transcriptDraft = text;
    } else if (payload.type === "error" || payload.type === "tool.error") {
      this.handlers.onError(payload.message || "实时语音请求失败");
    }
  }

  private releaseRecorder(): void {
    this.recorder?.disconnect();
    this.recorder = null;
    for (const track of this.mediaStream?.getTracks() || []) track.stop();
    this.mediaStream = null;
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
