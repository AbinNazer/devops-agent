import { useState, KeyboardEvent } from "react";
import { useChatStore } from "../../stores/chatStore";
import { useVoiceStore } from "../../stores/voiceStore";

export function ChatInput() {
  const [value, setValue] = useState("");
  const { sendMessage, isStreaming, stopGeneration, activeId } = useChatStore();
  const { openOverlay } = useVoiceStore();
  const submit = () => { const text = value.trim(); if (!text || !activeId || isStreaming) return; void sendMessage(text); setValue(""); };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } };
  return <div className="p-4 border-t border-jarvis-border"><div className="panel flex items-end gap-2 p-2">
    <button onClick={openOverlay} className="shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-jarvis-cyan hover:bg-jarvis-cyan/10 transition" title="Voice input">🎙️</button>
    <textarea value={value} onChange={(event) => setValue(event.target.value)} onKeyDown={onKeyDown} placeholder={activeId ? "Ask JARVIS about your infrastructure..." : "Start a new chat to begin"} disabled={!activeId || isStreaming} rows={1} className="flex-1 bg-transparent resize-none text-sm py-1.5 px-1 focus:outline-none placeholder:text-jarvis-muted max-h-40" />
    {isStreaming ? <button onClick={stopGeneration} className="shrink-0 px-4 py-1.5 rounded-lg bg-jarvis-crit/20 border border-jarvis-crit/50 text-jarvis-crit text-sm">■ Stop</button> : <button onClick={submit} disabled={!value.trim() || !activeId} className="shrink-0 px-4 py-1.5 rounded-lg bg-jarvis-cyan/20 border border-jarvis-cyan/50 text-jarvis-cyan text-sm disabled:opacity-30">Send</button>}
  </div></div>;
}
