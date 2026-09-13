export type Role = "user" | "assistant" | "system" | "tool";
export type JarvisActiveState = "idle" | "thinking" | "listening" | "running_tool" | "analyzing" | "responding" | "error" | "offline";
export interface ToolCall { id: string; name: string; arguments: Record<string, unknown>; result?: unknown; status: "running" | "success" | "error"; }
export interface Message { id: string; role: Role; content: string; toolCalls?: ToolCall[]; createdAt: string; }
export interface Conversation { id: string; title: string; createdAt: string; updatedAt: string; messages?: Message[]; }
export interface Provider { id: string; name: string; model?: string; available: boolean; status?: string; }
export interface ProvidersResponse { providers: Provider[]; active: string | null; }
export interface Task { id: string; conversationId: string; status: "running" | "done" | "error" | "cancelled"; description?: string; startedAt: string; }
export interface VoiceStatus { sttAvailable: boolean; ttsAvailable: boolean; }
export type StreamEvent = { type: "tool_call"; toolCall: ToolCall } | { type: "tool_result"; toolCallId: string; result: unknown; status: "success" | "error" } | { type: "text"; delta: string } | { type: "done"; message: Message } | { type: "error"; message: string };
export interface InfraStatusItem { label: string; status: "ok" | "warning" | "critical" | "unknown"; detail?: string; }
export interface InfraStatus { sections: Record<string, InfraStatusItem[]>; overall: "ok" | "warning" | "critical" | "unknown"; }
export type InfraSection = InfraStatusItem[];
