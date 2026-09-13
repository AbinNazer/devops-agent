import { useEffect, useRef, useState } from "react";
import { useVoiceStore } from "../../stores/voiceStore";
import { useChatStore } from "../../stores/chatStore";
import { voiceService } from "../../services/voiceService";
import { JarvisOrb } from "../core/JarvisCore";

const STOP_WORDS = /\b(bye|goodbye|stop voice chat|end voice chat)\b/i;

export function VoiceOverlay() {
  const voice = useVoiceStore();
  const chat = useChatStore();
  const recorderRef = useRef<MediaRecorder | null>(null);
  const silenceTimer = useRef<number | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const [voiceMode, setVoiceMode] = useState(false);
  const voiceModeRef = useRef(false);

  const clearAudio = () => {
    if (silenceTimer.current) window.clearTimeout(silenceTimer.current);
    silenceTimer.current = null;
    audioContext.current?.close().catch(() => undefined);
    audioContext.current = null;
  };
  const stopListening = () => {
    clearAudio();
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    voice.setRecording(false);
  };
  const endVoiceChat = () => {
    voiceModeRef.current = false;
    setVoiceMode(false);
    sessionStorage.removeItem("jarvisVoiceChat");
    window.speechSynthesis?.cancel();
    stopListening();
    voice.setTranscript("");
    voice.setError(null);
  };

  const beginListening = async () => {
    if (!voiceModeRef.current || recorderRef.current?.state === "recording" || chat.isStreaming || window.speechSynthesis?.speaking) return;
    voice.setError(null);
    try {
      const status = await voiceService.status();
      if (!status.sttAvailable) { voice.setError("Local Whisper is warming up. Retrying automatically…"); window.setTimeout(beginListening, 2000); return; }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const recorder = new MediaRecorder(stream, MediaRecorder.isTypeSupported("audio/webm") ? { mimeType: "audio/webm" } : undefined);
      let heardSpeech = false;
      const context = new AudioContext();
      const analyser = context.createAnalyser(); analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser);
      const samples = new Uint8Array(analyser.fftSize);
      const monitor = () => {
        if (recorder.state !== "recording") return;
        analyser.getByteTimeDomainData(samples);
        const level = samples.reduce((sum, item) => sum + Math.abs(item - 128), 0) / samples.length;
        if (level > 5) { heardSpeech = true; if (silenceTimer.current) window.clearTimeout(silenceTimer.current); silenceTimer.current = null; }
        else if (heardSpeech && !silenceTimer.current) silenceTimer.current = window.setTimeout(stopListening, 1300);
        requestAnimationFrame(monitor);
      };
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = async () => {
        clearAudio(); stream.getTracks().forEach((track) => track.stop()); recorderRef.current = null; voice.setRecording(false);
        if (!voiceModeRef.current || !chunks.length) return;
        voice.setTranscribing(true);
        try {
          const text = await voiceService.transcribe(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
          voice.setTranscript(text);
          if (!text.trim()) { window.setTimeout(beginListening, 500); return; }
          if (STOP_WORDS.test(text)) { endVoiceChat(); return; }
          sessionStorage.setItem("jarvisVoiceChat", "true");
          if (!chat.activeId) await chat.newConversation();
          await chat.sendMessage(text, "groq", "voice");
        } catch (error) { voice.setError(error instanceof Error ? error.message : "Transcription failed."); }
        finally { voice.setTranscribing(false); }
      };
      audioContext.current = context; recorderRef.current = recorder; recorder.start(); voice.setRecording(true); monitor();
    } catch (error) { voice.setError(error instanceof DOMException && error.name === "NotAllowedError" ? "Allow microphone access in browser settings." : "Microphone access is unavailable."); }
  };

  useEffect(() => {
    if (!voiceMode) return;
    const nextTurn = () => window.setTimeout(beginListening, 500);
    window.addEventListener("jarvis-speech-end", nextTurn);
    return () => { window.removeEventListener("jarvis-speech-end", nextTurn); };
  }, [voiceMode, chat.isStreaming]);
  useEffect(() => () => endVoiceChat(), []);

  const startVoiceChat = () => { voiceModeRef.current = true; setVoiceMode(true); sessionStorage.setItem("jarvisVoiceChat", "true"); window.setTimeout(beginListening, 0); };
  if (!voice.isOverlayOpen) return null;
  const state = voice.isRecording ? "Listening…" : voice.isTranscribing ? "Understanding…" : chat.isStreaming ? "JARVIS is thinking…" : window.speechSynthesis?.speaking ? "JARVIS is speaking…" : voiceMode ? "Waiting for your next question…" : "Start a hands-free voice chat";
  return <div className="fixed inset-0 z-50 bg-jarvis-bg/98 backdrop-blur-md flex flex-col items-center justify-between py-16 animate-fade-in">
    <button onClick={voice.closeOverlay} className="absolute top-6 right-6 text-2xl text-jarvis-muted hover:text-jarvis-text" aria-label="Close voice chat">×</button>
    <div />
    <div className="flex flex-col items-center gap-6"><JarvisOrb size={180} active={voiceMode || voice.isTranscribing || chat.isStreaming} /><p className="text-jarvis-muted text-sm">{state}</p>{voice.transcript && <p className="max-w-lg text-center text-jarvis-text text-sm">You: “{voice.transcript}”</p>}{voice.error && <p className="max-w-lg text-center text-jarvis-crit text-sm">{voice.error}</p>}</div>
    <button onClick={voiceMode ? endVoiceChat : startVoiceChat} className={`rounded-xl px-6 py-3 font-medium ${voiceMode ? "bg-jarvis-crit/20 border border-jarvis-crit/60 text-jarvis-crit" : "bg-jarvis-cyan/20 border border-jarvis-cyan/60 text-jarvis-cyan"}`}>{voiceMode ? "■ Stop voice chat" : "🎙️ Start voice chat"}</button>
  </div>;
}