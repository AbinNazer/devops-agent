import { useEffect } from "react";
import { useTaskStore } from "../../stores/taskStore";

export function TaskPanel() {
  const { tasks, load, cancel } = useTaskStore();

  useEffect(() => {
    load();
    const interval = setInterval(load, 15_000);
    return () => clearInterval(interval);
  }, []);

  const running = tasks.filter((t) => t.status === "running");
  if (running.length === 0) return null;

  return (
    <div className="absolute top-16 right-4 w-72 space-y-2 z-40">
      {running.map((t) => (
        <div key={t.id} className="panel px-3 py-2 flex items-center justify-between text-xs animate-fade-in">
          <div className="flex items-center gap-2 min-w-0">
            <span className="status-dot status-warn animate-pulse" />
            <span className="truncate text-jarvis-text">{t.description || "Running task"}</span>
          </div>
          <button
            onClick={() => cancel(t.id)}
            className="shrink-0 ml-2 text-jarvis-muted hover:text-jarvis-crit"
            title="Cancel task"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}