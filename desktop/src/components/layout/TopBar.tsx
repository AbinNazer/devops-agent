import { useEffect, useState } from "react";
import { SettingsModal } from "../settings/SettingsModal";
import { useProviderStore } from "../../stores/providerStore";
import { useAppStore } from "../../stores/appStore";

export function TopBar() {
  const { providers, activeId, load, switchTo } = useProviderStore();
  const { backendOnline, checkBackend } = useAppStore();
  const [settingsOpen, setSettingsOpen] = useState(false);
  useEffect(() => { load(); checkBackend(); const interval = setInterval(checkBackend, 10_000); return () => clearInterval(interval); }, []);
  return <>
    <div className="h-14 flex items-center justify-between px-5 border-b border-jarvis-border bg-jarvis-panel/60 backdrop-blur">
      <div className="flex items-center gap-2"><span className="status-dot status-ok" /><span className="font-bold tracking-widest text-jarvis-cyan">J.A.R.V.I.S</span></div>
      <div className="flex items-center gap-2">{providers.length === 0 ? <span className="text-sm text-jarvis-muted">No provider configured</span> : <select value={activeId ?? ""} onChange={(event) => switchTo(event.target.value)} className="bg-jarvis-bg border border-jarvis-border rounded-lg px-3 py-1.5 text-sm text-jarvis-text"><>{providers.map((provider) => <option key={provider.id} value={provider.id} disabled={!provider.available}>{provider.name}{provider.model ? ` (${provider.model})` : ""}{!provider.available ? " — unavailable" : ""}</option>)}</></select>}</div>
      <div className="flex items-center gap-3 text-sm"><button onClick={() => setSettingsOpen(true)} className="text-jarvis-muted hover:text-jarvis-cyan" title="Settings">Settings</button><span className={`status-dot ${backendOnline ? "status-ok" : "status-crit"}`} /><span className={backendOnline ? "text-jarvis-ok" : "text-jarvis-crit"}>{backendOnline ? "ONLINE" : "OFFLINE"}</span></div>
    </div>
    {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
  </>;
}