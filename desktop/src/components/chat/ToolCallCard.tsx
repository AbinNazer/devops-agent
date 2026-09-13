import { useState } from "react";
import type { ToolCall } from "../../types";

export function ToolCallCard({ toolCall }: { toolCall: ToolCall }) {
  const [open, setOpen] = useState(false);

  const statusColor =
    toolCall.status === "success" ? "text-jarvis-ok" : toolCall.status === "error" ? "text-jarvis-crit" : "text-jarvis-warn";

  return (
    <div className="panel my-1.5 text-xs overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-jarvis-border/30 transition"
      >
        <span className="flex items-center gap-2">
          <span className={statusColor}>●</span>
          <span className="font-mono text-jarvis-cyan">{toolCall.name}</span>
          <span className="text-jarvis-muted">
            {toolCall.status === "running" ? "running…" : toolCall.status}
          </span>
        </span>
        <span className="text-jarvis-muted">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-2 animate-fade-in">
          <div>
            <div className="text-jarvis-muted mb-1">Arguments</div>
            <pre className="bg-jarvis-bg rounded p-2 overflow-x-auto">{JSON.stringify(toolCall.arguments, null, 2)}</pre>
          </div>
          {toolCall.result !== undefined && (
            <div>
              <div className="text-jarvis-muted mb-1">Result</div>
              <pre className="bg-jarvis-bg rounded p-2 overflow-x-auto max-h-64">
                {typeof toolCall.result === "string" ? toolCall.result : JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}