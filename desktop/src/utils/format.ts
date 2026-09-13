export function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const diffMin = Math.floor(diffMs / 60_000);

  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

export function statusColor(status: "ok" | "warning" | "critical" | "unknown"): string {
  switch (status) {
    case "ok":
      return "status-ok";
    case "warning":
      return "status-warn";
    case "critical":
      return "status-crit";
    default:
      return "status-unknown";
  }
}