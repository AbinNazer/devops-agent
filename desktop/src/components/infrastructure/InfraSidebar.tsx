import { useEffect } from "react";
import { useAppStore } from "../../stores/appStore";
import { statusColor } from "../../utils/format";

export function InfraSidebar() {
  const { infra, infraError, loadInfra } = useAppStore();

  useEffect(() => {
    loadInfra();
    const interval = setInterval(loadInfra, 30_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="w-60 border-l border-jarvis-border bg-jarvis-panel/40 p-4 overflow-y-auto">
      <h3 className="text-xs font-semibold tracking-widest text-jarvis-muted mb-3">INFRASTRUCTURE</h3>

      {infraError && (
        <div className="text-center py-8">
          <p className="text-jarvis-crit text-sm font-medium">No infrastructure data</p>
          <p className="text-jarvis-muted text-xs mt-1">Send a health check request to JARVIS</p>
        </div>
      )}

      {!infraError && !infra && <p className="text-jarvis-muted text-xs">Loading…</p>}

      {infra &&
        Object.entries(infra.sections).map(([section, items]) => (
          <div key={section} className="mb-4">
            <h4 className="text-xs text-jarvis-muted mb-1.5">{section}</h4>
            <div className="space-y-1">
              {items.map((item) => (
                <div key={item.label} className="flex items-center gap-2 text-xs">
                  <span className={`status-dot ${statusColor(item.status)}`} />
                  <span className="truncate">{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}