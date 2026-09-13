import { create } from "zustand";
import { voiceService } from "../services/voiceService";

interface VoiceState {
  sttAvailable: boolean;
  ttsAvailable: boolean;
  isOverlayOpen: boolean;
  isRecording: boolean;
  isTranscribing: boolean;
  transcript: string;
  error: string | null;

  loadStatus: () => Promise<void>;
  openOverlay: () => void;
  closeOverlay: () => void;
  setRecording: (recording: boolean) => void;
  setTranscribing: (transcribing: boolean) => void;
  setTranscript: (text: string) => void;
  setError: (err: string | null) => void;
}

export const useVoiceStore = create<VoiceState>((set) => ({
  sttAvailable: false,
  ttsAvailable: false,
  isOverlayOpen: false,
  isRecording: false,
  isTranscribing: false,
  transcript: "",
  error: null,

  loadStatus: async () => {
    try {
      const status = await voiceService.status();
      set({ sttAvailable: status.sttAvailable, ttsAvailable: status.ttsAvailable });
    } catch {
      set({ sttAvailable: false, ttsAvailable: false });
    }
  },

  openOverlay: () => set({ isOverlayOpen: true, transcript: "", error: null }),
  closeOverlay: () => set({ isOverlayOpen: false, isRecording: false, isTranscribing: false }),
  setRecording: (isRecording) => set({ isRecording }),
  setTranscribing: (isTranscribing) => set({ isTranscribing }),
  setTranscript: (transcript) => set({ transcript }),
  setError: (error) => set({ error }),
}));