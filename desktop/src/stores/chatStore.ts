import { create } from "zustand";
import type { Conversation, Message, StreamEvent } from "../types";
import { chatService } from "../services/chatService";
import { voiceService } from "../services/voiceService";

interface ChatState {
  conversations: Conversation[];
  activeId: string | null;
  isStreaming: boolean;
  streamController: AbortController | null;
  error: string | null;

  loadConversations: () => Promise<void>;
  selectConversation: (id: string) => Promise<void>;
  newConversation: () => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  searchConversations: (query: string) => Promise<Conversation[]>;

  sendMessage: (content: string, provider?: string, inputMode?: "text" | "voice") => Promise<void>;
  stopGeneration: () => void;
}

function upsertMessage(messages: Message[], partial: Partial<Message> & { id: string }): Message[] {
  const idx = messages.findIndex((m) => m.id === partial.id);
  if (idx === -1) {
    return [...messages, { role: "assistant", content: "", createdAt: new Date().toISOString(), ...partial } as Message];
  }
  const updated = [...messages];
  updated[idx] = { ...updated[idx], ...partial };
  return updated;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeId: null,
  isStreaming: false,
  streamController: null,
  error: null,

  loadConversations: async () => {
    try {
      const conversations = await chatService.list();
      set({ conversations });
      if (!get().activeId && conversations.length > 0) {
        await get().selectConversation(conversations[0].id);
      }
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  selectConversation: async (id: string) => {
    try {
      const full = await chatService.get(id);
      set((state) => ({
        activeId: id,
        conversations: state.conversations.map((c) => (c.id === id ? full : c)),
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  newConversation: async () => {
    try {
      const conv = await chatService.create();
      set((state) => ({ conversations: [conv, ...state.conversations], activeId: conv.id }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  renameConversation: async (id: string, title: string) => {
    try {
      const updated = await chatService.rename(id, title);
      set((state) => ({
        conversations: state.conversations.map((c) => (c.id === id ? { ...c, title: updated.title } : c)),
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  deleteConversation: async (id: string) => {
    try {
      await chatService.remove(id);
      set((state) => {
        const remaining = state.conversations.filter((c) => c.id !== id);
        const activeId = state.activeId === id ? remaining[0]?.id ?? null : state.activeId;
        return { conversations: remaining, activeId };
      });
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  searchConversations: async (query: string) => {
    try {
      return await chatService.search(query);
    } catch (err) {
      set({ error: (err as Error).message });
      return [];
    }
  },

  sendMessage: async (content: string, provider?: string, inputMode: "text" | "voice" = "text") => {
    const { activeId } = get();
    if (!activeId) return;

    const userMsg: Message = {
      id: `local-${Date.now()}`,
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    const assistantId = `stream-${Date.now()}`;

    set((state) => ({
      isStreaming: true,
      error: null,
      conversations: state.conversations.map((c) =>
        c.id === activeId
          ? { ...c, messages: [...(c.messages ?? []), userMsg, { id: assistantId, role: "assistant", content: "", toolCalls: [], createdAt: new Date().toISOString() }] }
          : c
      ),
    }));

    const applyToActive = (fn: (messages: Message[]) => Message[]) =>
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === activeId ? { ...c, messages: fn(c.messages ?? []) } : c
        ),
      }));

    const controller = chatService.streamMessage(
      activeId,
      content,
      (event: StreamEvent) => {
        if (event.type === "text") {
          applyToActive((messages) => {
            const msg = messages.find((m) => m.id === assistantId);
            return upsertMessage(messages, { id: assistantId, content: (msg?.content ?? "") + event.delta });
          });
        } else if (event.type === "tool_call") {
          applyToActive((messages) => {
            const msg = messages.find((m) => m.id === assistantId);
            const toolCalls = [...(msg?.toolCalls ?? []), event.toolCall];
            return upsertMessage(messages, { id: assistantId, toolCalls });
          });
        } else if (event.type === "tool_result") {
          applyToActive((messages) => {
            const msg = messages.find((m) => m.id === assistantId);
            const toolCalls = (msg?.toolCalls ?? []).map((tc) =>
              tc.id === event.toolCallId ? { ...tc, result: event.result, status: event.status } : tc
            );
            return upsertMessage(messages, { id: assistantId, toolCalls });
          });
        } else if (event.type === "error") {
          set({ error: event.message });
        }
        // "done" is handled in onDone below, which re-fetches authoritative state
      },
      (err) => {
        set({ error: err.message, isStreaming: false, streamController: null });
      },
      async () => {
        set({ isStreaming: false, streamController: null });
        // Re-fetch from backend for authoritative state, as documented in README step 12
        try {
await get().selectConversation(activeId);
          // Voice overlay opts into hands-free replies for this browser tab.
          if (sessionStorage.getItem("jarvisVoiceChat") === "true" && localStorage.getItem("jarvisAutoSpeak") !== "false" && "speechSynthesis" in window) {
            const conversation = get().conversations.find((item) => item.id === activeId);
            const reply = [...(conversation?.messages ?? [])].reverse().find((item) => item.role === "assistant" && item.content.trim());
            if (reply) {
              window.speechSynthesis.cancel();
              const text = reply.content.replace(/```[\s\S]*?```/g, "Code block omitted.");
              try {
                const audioUrl = await voiceService.synthesize(text);
                const audio = new Audio(audioUrl);
                audio.onended = () => { URL.revokeObjectURL(audioUrl); window.dispatchEvent(new Event("jarvis-speech-end")); };
                audio.onerror = () => { URL.revokeObjectURL(audioUrl); window.dispatchEvent(new Event("jarvis-speech-end")); };
                await audio.play();
              } catch {
                const utterance = new SpeechSynthesisUtterance(text);
                utterance.rate = Number(localStorage.getItem("jarvisSpeechRate") || "1");
                utterance.onend = () => window.dispatchEvent(new Event("jarvis-speech-end"));
                utterance.onerror = () => window.dispatchEvent(new Event("jarvis-speech-end"));
                window.speechSynthesis.speak(utterance);
              }
            }
          }
        } catch {
          /* keep optimistic state if refetch fails */
        }
      },
      provider,
      inputMode
    );

    set({ streamController: controller });
  },

  stopGeneration: () => {
    get().streamController?.abort();
    set({ isStreaming: false, streamController: null });
  },
}));