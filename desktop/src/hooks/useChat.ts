import { useChatStore } from "../stores/chatStore";
export function useChat() {
  const sendMessage = useChatStore((state) => state.sendMessage);
  const stopGeneration = useChatStore((state) => state.stopGeneration);
  const isStreaming = useChatStore((state) => state.isStreaming);
  return { sendMessage, stopGeneration, isStreaming };
}
