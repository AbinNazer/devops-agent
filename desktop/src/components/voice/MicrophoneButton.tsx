import { useVoiceStore } from "../../stores/voiceStore";
export function MicrophoneButton() {
  const { sttAvailable, openOverlay } = useVoiceStore();
  if (!sttAvailable) return null;
  return <button type="button" onClick={openOverlay} className="btn-ghost" title="Voice input">🎙️</button>;
}
