import { useMemo, useState } from "react";
import { useChatStore } from "../../stores/chatStore";

export function Sidebar() {
  const { conversations, activeId, newConversation, selectConversation, renameConversation, deleteConversation, searchConversations } = useChatStore();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(true);
  const [results, setResults] = useState<typeof conversations | null>(null);

  const visibleConversations = useMemo(() => results ?? conversations, [conversations, results]);
  const search = async (value: string) => {
    setQuery(value);
    setResults(value.trim() ? await searchConversations(value) : null);
  };
  const rename = async (id: string, current: string) => {
    const title = window.prompt("Conversation title", current)?.trim();
    if (title && title !== current) await renameConversation(id, title);
  };

  if (collapsed) {
    return <aside className="w-14 shrink-0 border-r border-jarvis-border bg-jarvis-panel flex flex-col items-center gap-3 py-3"><button className="btn-ghost" onClick={() => setCollapsed(false)} title="Expand sidebar">☰</button><button className="btn-primary px-3" onClick={() => void newConversation()} title="New chat">+</button></aside>;
  }

  return (
    <aside className="w-64 shrink-0 border-r border-jarvis-border bg-jarvis-panel flex flex-col min-h-0">
      <div className="p-4 border-b border-jarvis-border">
        <div className="flex items-center justify-between mb-3"><span className="font-bold tracking-widest text-jarvis-cyan">JARVIS</span><button className="btn-ghost" onClick={() => setCollapsed(true)} title="Collapse sidebar">☰</button></div>
        <button className="btn-primary w-full text-sm" onClick={() => void newConversation()}>+ New chat</button>
      </div>
      <div className="p-3"><input value={query} onChange={(event) => void search(event.target.value)} placeholder="Search conversations" className="w-full bg-jarvis-bg border border-jarvis-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-jarvis-cyan" /></div>
      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-1">
        {visibleConversations.length === 0 ? <p className="text-xs text-jarvis-muted text-center mt-8">No conversations yet</p> : visibleConversations.map((conversation) => (
          <div key={conversation.id} className={`group flex items-center gap-1 rounded-lg px-2 py-2 cursor-pointer text-sm ${activeId === conversation.id ? "bg-jarvis-cyan/10 text-jarvis-cyan" : "hover:bg-jarvis-bg text-jarvis-text"}`} onClick={() => void selectConversation(conversation.id)}>
            <span className="truncate flex-1">{conversation.title}</span>
            <button className="hidden group-hover:block text-jarvis-muted hover:text-jarvis-text" onClick={(event) => { event.stopPropagation(); void rename(conversation.id, conversation.title); }} title="Rename">✎</button>
            <button className="hidden group-hover:block text-jarvis-muted hover:text-jarvis-crit" onClick={(event) => { event.stopPropagation(); if (window.confirm(`Delete "${conversation.title}"?`)) void deleteConversation(conversation.id); }} title="Delete">×</button>
          </div>
        ))}
      </div>
    </aside>
  );
}
