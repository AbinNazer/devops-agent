import { useEffect, useState } from "react";
import { useChatStore } from "../../stores/chatStore";
import type { Conversation } from "../../types";
import { formatRelativeTime } from "../../utils/format";

export function ConversationSidebar() {
  const { conversations, activeId, loadConversations, selectConversation, newConversation, renameConversation, deleteConversation, searchConversations } = useChatStore();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Conversation[] | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    if (!query.trim()) {
      setResults(null);
      return;
    }
    const t = setTimeout(async () => setResults(await searchConversations(query)), 250);
    return () => clearTimeout(t);
  }, [query]);

  const list = results ?? conversations;

  const startRename = (c: Conversation) => {
    setRenamingId(c.id);
    setRenameValue(c.title);
  };

  const commitRename = async () => {
    if (renamingId && renameValue.trim()) {
      await renameConversation(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  };

  return (
    <div className="w-64 flex flex-col border-r border-jarvis-border bg-jarvis-panel/40">
      <div className="p-3 space-y-2">
        <button
          onClick={() => newConversation()}
          className="w-full py-2 rounded-lg bg-jarvis-cyan/10 border border-jarvis-cyan/40 text-jarvis-cyan text-sm font-medium hover:bg-jarvis-cyan/20 transition"
        >
          + New chat
        </button>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations..."
          className="w-full bg-jarvis-bg border border-jarvis-border rounded-lg px-3 py-1.5 text-sm placeholder:text-jarvis-muted focus:outline-none focus:ring-1 focus:ring-jarvis-cyan"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-2 space-y-1">
        {list.length === 0 && (
          <p className="text-xs text-jarvis-muted px-2 py-4 text-center">
            {query ? "No matches." : "No conversations yet."}
          </p>
        )}
        {list.map((c) => (
          <div
            key={c.id}
            className={`group rounded-lg px-3 py-2 cursor-pointer transition ${
              c.id === activeId ? "bg-jarvis-cyan/15 border border-jarvis-cyan/30" : "hover:bg-jarvis-border/40 border border-transparent"
            }`}
            onClick={() => selectConversation(c.id)}
          >
            {renamingId === c.id ? (
              <input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => e.key === "Enter" && commitRename()}
                onClick={(e) => e.stopPropagation()}
                className="w-full bg-transparent border-b border-jarvis-cyan text-sm focus:outline-none"
              />
            ) : (
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm truncate">{c.title || "Untitled chat"}</span>
                <div className="hidden group-hover:flex gap-1 shrink-0">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      startRename(c);
                    }}
                    className="text-xs text-jarvis-muted hover:text-jarvis-cyan"
                    title="Rename"
                  >
                    ✎
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete "${c.title || "this chat"}"?`)) deleteConversation(c.id);
                    }}
                    className="text-xs text-jarvis-muted hover:text-jarvis-crit"
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}
            <span className="text-[10px] text-jarvis-muted">{formatRelativeTime(c.updatedAt)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}