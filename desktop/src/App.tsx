import { useEffect } from "react";
import { TopBar } from "./components/layout/TopBar";
import { Sidebar } from "./components/shell/Sidebar";
import { ChatPanel } from "./components/chat/ChatPanel";
import { InfraSidebar } from "./components/infrastructure/InfraSidebar";
import { TaskPanel } from "./components/tasks/TaskPanel";
import { VoiceOverlay } from "./components/voice/VoiceOverlay";
import { useVoiceStore } from "./stores/voiceStore";
import { useChatStore } from "./stores/chatStore";

export default function App() {
  const loadVoiceStatus = useVoiceStore((s) => s.loadStatus);
  const loadConversations = useChatStore((s) => s.loadConversations);

  useEffect(() => {
    void loadVoiceStatus();
    void loadConversations();
  }, [loadConversations, loadVoiceStatus]);

  return (
    <div className="h-screen flex flex-col bg-jarvis-bg relative">
      <TopBar />
      <div className="flex-1 flex min-h-0 relative">
        <Sidebar />
        <ChatPanel />
        <InfraSidebar />
        <TaskPanel />
      </div>
      <VoiceOverlay />
    </div>
  );
}
