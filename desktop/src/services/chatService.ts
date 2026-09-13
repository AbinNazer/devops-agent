import { api } from "./api";
import type { Conversation, StreamEvent } from "../types";

export const chatService = {
  list: () => api.get<Conversation[]>("/conversations"),
  create: (title?: string) => api.post<Conversation>(`/conversations${title ? `?title=${encodeURIComponent(title)}` : ""}`),
  get: (id: string) => api.get<Conversation>(`/conversations/${id}`),
  search: (query: string) => api.get<Conversation[]>(`/conversations/search?q=${encodeURIComponent(query)}`),
  rename: async (id: string, title: string) => {
    await api.put<{ success: boolean }>(`/conversations/${id}/rename`, { title });
    return api.get<Conversation>(`/conversations/${id}`);
  },
  remove: (id: string) => api.delete<void>(`/conversations/${id}`),
  streamMessage(conversationId: string, content: string, onEvent: (event: StreamEvent) => void, onError: (err: Error) => void, onDone: () => void, provider?: string, inputMode = "text"): AbortController {
    return api.postStream(`/conversations/${conversationId}/chat/stream`, { message: content, provider, input_mode: inputMode }, (raw) => {
      try {
        const event = JSON.parse(raw) as Record<string, unknown>;
        if (event.type === "text") onEvent({ type: "text", delta: String(event.content ?? "") });
        else if (event.type === "error") onEvent({ type: "error", message: String(event.content ?? "Unknown error") });
        else if (event.type === "tool_call") onEvent({ type: "tool_call", toolCall: { id: crypto.randomUUID(), name: String(event.name ?? "tool"), arguments: (event.arguments as Record<string, unknown>) ?? {}, status: "running" } });
        else if (event.type === "done") onEvent({ type: "done", message: { id: String(event.message_id ?? "done"), role: "assistant", content: "", createdAt: new Date().toISOString() } });
      } catch { onEvent({ type: "error", message: "Malformed response from backend" }); }
    }, onError, onDone);
  },
  sendMessage: (conversationId: string, content: string) => api.post<Conversation>(`/conversations/${conversationId}/chat`, { message: content }),
};
