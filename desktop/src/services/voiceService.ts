import { api } from "./api";
import type { VoiceStatus } from "../types";
const BASE = (import.meta as any).env?.VITE_API_BASE || "/api";
export const voiceService = {
  status: async (): Promise<VoiceStatus> => {
    const status = await api.get<{ stt_available: boolean; tts_available: boolean }>("/voice/status");
    return { sttAvailable: status.stt_available, ttsAvailable: status.tts_available };
  },
  async transcribe(blob: Blob): Promise<string> {
    const response = await fetch(`${BASE}/voice/transcribe`, { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob });
    if (!response.ok) throw new Error(`Transcription failed: ${response.statusText}`);
    return (await response.json()).text ?? "";
  },
  async synthesize(text: string): Promise<string> {
    const response = await fetch(`${BASE}/voice/synthesize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!response.ok) throw new Error(`Synthesis failed: ${response.statusText}`);
    return URL.createObjectURL(await response.blob());
  },
};
