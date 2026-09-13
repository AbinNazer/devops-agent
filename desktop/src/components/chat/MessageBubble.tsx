import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Message } from "../../types";
import { ToolCallCard } from "./ToolCallCard";

function speak(text: string) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.replace(/```[\s\S]*?```/g, "Code block omitted.").replace(/[#*_`]/g, " "));
  utterance.rate = 1;
  window.speechSynthesis.speak(utterance);
}

export function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const [speaking, setSpeaking] = useState(false);
  const toggleSpeech = () => {
    if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; }
    setSpeaking(true);
    speak(message.content);
    window.speechSynthesis.onvoiceschanged = () => setSpeaking(false);
    window.setTimeout(() => setSpeaking(false), Math.max(1500, message.content.length * 70));
  };
  return <div className={`flex ${isUser ? "justify-end" : "justify-start"} animate-fade-in`}><div className={`max-w-[75%] ${isUser ? "" : "w-full"}`}>
    {!isUser && message.toolCalls?.map((toolCall) => <ToolCallCard key={toolCall.id} toolCall={toolCall} />)}
    <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${isUser ? "bg-jarvis-cyan/15 border border-jarvis-cyan/30 text-jarvis-text" : "panel text-jarvis-text"}`}>
      {message.content ? <ReactMarkdown>{message.content}</ReactMarkdown> : <span className="text-jarvis-muted animate-pulse">JARVIS is thinking…</span>}
      {!isUser && message.content && "speechSynthesis" in window && <button onClick={toggleSpeech} className="mt-2 text-xs text-jarvis-cyan hover:text-jarvis-text" aria-label="Speak assistant response">{speaking ? "■ Stop speaking" : "🔊 Speak"}</button>}
    </div>
  </div></div>;
}