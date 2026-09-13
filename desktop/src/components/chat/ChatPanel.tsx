import { useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import { JarvisOrb } from "../core/JarvisCore";

const QUICK_ACTIONS = [
  { icon: "◉", label: "Check infrastructure health", prompt: "Check everything and tell me if anything is wrong." },
  { icon: "🐳", label: "What Docker containers are running?", prompt: "Are my Docker containers okay?" },
  { icon: "🖥", label: "Check VPS status", prompt: "What's my server uptime and load?" },
  { icon: "📊", label: "Analyze system performance", prompt: "Why is my VPS slow?" },
];

export function ChatPanel() {
  const { conversations, activeId, sendMessage, newConversation } = useChatStore();
  const active = conversations.find((c) => c.id === activeId);
  const messages = active?.messages ?? [];
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.content]);

  const runQuickAction = async (prompt: string) => {
    if (!activeId) await newConversation();
    sendMessage(prompt);
  };

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center gap-6 animate-fade-in">
            <JarvisOrb size={140} />
            <div className="text-center">
              <h1 className="text-2xl font-bold text-jarvis-cyan tracking-wide">J.A.R.V.I.S</h1>
              <p className="text-jarvis-muted text-sm mt-1">AI-Powered DevOps Command Center</p>
            </div>
            <div className="flex flex-col gap-2 w-full max-w-md">
              {QUICK_ACTIONS.map((qa) => (
                <button
                  key={qa.label}
                  onClick={() => runQuickAction(qa.prompt)}
                  className="panel flex items-center gap-3 px-4 py-3 text-sm text-left hover:border-jarvis-cyan/40 hover:bg-jarvis-cyan/5 transition"
                >
                  <span>{qa.icon}</span>
                  <span>{qa.label}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto space-y-4">
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
      <ChatInput />
    </div>
  );
}