"""Read-only web research and local skill profiles.

Research is knowledge, not permission to execute anything discovered online.
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

import requests

logger = logging.getLogger("research")
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
RESEARCH_CACHE_DIR = SKILLS_DIR / ".cache"
OFFICIAL_DOMAINS = {
    "docker": ("docs.docker.com", "github.com/docker"),
    "kubernetes": ("kubernetes.io", "github.com/kubernetes"),
    "redis": ("redis.io", "github.com/redis"),
    "prometheus": ("prometheus.io", "github.com/prometheus"),
    "terraform": ("developer.hashicorp.com", "github.com/hashicorp"),
}


def _official_first(tool: str, url: str) -> int:
    domains = OFFICIAL_DOMAINS.get(tool.lower(), ())
    return 0 if any(domain in url for domain in domains) else 1


def web_search(query: str, max_results: int = 5, domains: list[str] | None = None, recency: int | None = None) -> dict:
    """Search DuckDuckGo's public HTML endpoint and return safe metadata only."""
    if not query or not query.strip():
        return {"success": False, "error": "query is required", "results": []}
    search_query = query.strip()
    if domains:
        search_query += " " + " ".join(f"site:{domain}" for domain in domains)
    if recency:
        search_query += f" after:{max(1, int(recency))}d"
    try:
        response = requests.get("https://html.duckduckgo.com/html/?q=" + quote(search_query), timeout=10, headers={"User-Agent": "JARVIS-Research/1.0"})
        response.raise_for_status()
        blocks = re.findall(r'<div class="result results_links results_links_deep web-result[^>]*>(.*?)</div>\s*</div>', response.text, re.S)
        results = []
        for block in blocks[: max(1, min(int(max_results), 10))]:
            link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a?>', block, re.S)
            if not link:
                continue
            url = html.unescape(link.group(1))
            if "uddg=" in url:
                url = unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
            results.append({"title": re.sub(r"<[^>]+>", "", html.unescape(link.group(2))).strip(), "url": url, "snippet": re.sub(r"<[^>]+>", "", html.unescape(snippet.group(1) if snippet else "")).strip(), "source": url.split("/", 3)[2] if "://" in url else ""})
        results.sort(key=lambda item: _official_first(search_query, item["url"]))
        logger.info("web_search_completed query=%r results=%d", query, len(results))
        return {"success": True, "query": query, "results": results}
    except requests.RequestException as exc:
        logger.warning("web_search_failed query=%r error=%s", query, exc)
        return {"success": False, "error": "Web search could not be verified right now", "results": []}


def fetch_documentation(url: str, max_chars: int = 12000) -> dict:
    """Fetch a public documentation page as bounded, non-executable text."""
    if not url.startswith(("https://", "http://")):
        return {"success": False, "error": "Only HTTP(S) documentation URLs are supported"}
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "JARVIS-Research/1.0"})
        response.raise_for_status()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.I | re.S)
        text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", response.text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        return {"success": True, "url": url, "title": re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip() if title_match else url, "content": text[:max(1000, min(max_chars, 20000))]}
    except requests.RequestException as exc:
        logger.warning("documentation_fetch_failed url=%r error=%s", url, exc)
        return {"success": False, "url": url, "error": "Documentation could not be verified"}


VERSION_COMMANDS = {
    "docker": ("docker", "--version"), "kubectl": ("kubectl", "version", "--client", "--output=yaml"),
    "kubernetes": ("kubectl", "version", "--client", "--output=yaml"), "redis": ("redis-server", "--version"),
    "prometheus": ("prometheus", "--version"), "terraform": ("terraform", "version"),
    "helm": ("helm", "version", "--short"), "caddy": ("caddy", "version"),
}


def detect_local_version(tool: str) -> dict:
    key = tool.lower().strip()
    command = VERSION_COMMANDS.get(key, (key, "--version"))
    executable = shutil.which(command[0])
    if not executable:
        return {"status": "not_installed_or_unavailable", "tool": tool}
    try:
        completed = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=5, check=False)
        output = (completed.stdout or completed.stderr).strip()
        return {"status": "detected" if completed.returncode == 0 and output else "unknown", "tool": tool, "executable": executable, "version": output[:500]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unknown", "tool": tool, "error": str(exc)}


def correlate_project(tool: str, root: Path | None = None, max_files: int = 80) -> dict:
    base = root or Path.cwd()
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache"}
    secret_names = {".env", ".env.local", "id_rsa", "id_ed25519"}
    matches = []
    pattern = re.compile(re.escape(tool), re.I)
    for path in base.rglob("*"):
        if len(matches) >= max_files or not path.is_file() or any(part in ignored for part in path.parts) or path.name in secret_names or path.suffix in {".db", ".sqlite", ".pyc", ".pem", ".key"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:200_000]
        except OSError:
            continue
        if pattern.search(text):
            matches.append({"file": str(path.relative_to(base)), "references": len(pattern.findall(text))})
    return {"root": str(base), "matches": matches, "status": "found" if matches else "not_found"}


def compare_sources(pages: list[dict]) -> dict:
    successful = [p for p in pages if p.get("documentation", {}).get("success")]
    if len(successful) < 2:
        return {"status": "insufficient_sources", "sources_compared": len(successful), "conflicts": []}
    snippets = [re.sub(r"\W+", " ", p.get("snippet", "")).lower().strip() for p in successful]
    return {"status": "needs_review" if len(set(snippets)) == len(snippets) else "partially_agreeing", "sources_compared": len(successful), "conflicts": []}


def inspect_existing_infrastructure(tool: str) -> dict:
    """Use existing read-only infrastructure functions when a safe mapping exists."""
    try:
        if tool.lower() == "docker":
            from app.tools.docker import docker_status
            return {"tool": tool, "source": "existing docker tool", "evidence": docker_status()}
        if tool.lower() in {"kubernetes", "k3s"}:
            from app.tools.k3s import get_k3s_cluster_status
            return {"tool": tool, "source": "existing k3s tool", "evidence": get_k3s_cluster_status()}
    except Exception as exc:
        return {"tool": tool, "status": "inspection_failed", "error": str(exc)}
    return {"tool": tool, "status": "no_existing_read_only_inspector"}


def learn_tool(tool: str, refresh: bool = False, version: str = "") -> dict:
    """Create/update a source-tracked, non-executable tool knowledge profile."""
    name = (tool or "").strip()
    if not name:
        return {"success": False, "error": "tool is required"}
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    path = SKILLS_DIR / slug / "SKILL.md"
    cache_path = RESEARCH_CACHE_DIR / f"{slug}.json"
    if path.exists() and cache_path.exists() and not refresh:
        return {"success": True, "status": "already_known", "skill": str(path.relative_to(SKILLS_DIR.parent))}
    result = web_search(f"{name} official documentation CLI configuration monitoring troubleshooting security", max_results=6, domains=list(OFFICIAL_DOMAINS.get(name.lower(), ())))
    if not result["success"]:
        return {"success": False, "error": result["error"], "status": "unverified"}
    researched_at = datetime.now(timezone.utc).isoformat()
    local = detect_local_version(name)
    project = correlate_project(name)
    infrastructure = inspect_existing_infrastructure(name)
    pages = []
    for item in result["results"][:4]:
        page = fetch_documentation(item["url"])
        pages.append({**item, "documentation": page})
    sources = "\n".join(f"- [{item['title']}]({item['url']}) — {item['source']}" for item in pages)
    source_comparison = compare_sources(pages)
    notes = "\n".join("- " + item["snippet"] for item in pages if item["snippet"])
    docs = "\n\n".join(f"### {item['documentation'].get('title', item['title'])}\n{item['documentation'].get('content', item['snippet'])[:1800]}" for item in pages if item['documentation'].get('success'))
    compatibility = version or local.get("version", "unknown (provide the installed version for compatibility checking)")
    content = f"# {name}\n\n> Research profile only. Commands found here require normal JARVIS permission and approval checks.\n\n- Researched: `{researched_at}`\n- Requested/local version: `{compatibility}`\n- Knowledge status: `researched`, not locally verified\n- Source comparison: `{source_comparison['status']}`\n\n## Sources\n\n{sources or '- No sources returned.'}\n\n## Research notes\n\n{notes or '- Read the linked official documentation before relying on this profile.'}\n\n## Documentation excerpts\n\n{docs or '- Documentation pages could not be read; use the linked sources directly.'}\n\n## Local version evidence\n\n```json\n{json.dumps(local, indent=2)}\n```\n\n## Project correlation\n\n```json\n{json.dumps(project, indent=2)}\n```\n\n## Existing infrastructure evidence\n\n```json\n{json.dumps(infrastructure, indent=2, default=str)[:8000]}\n```\n\n## Compatibility and source comparison\n\nDocumentation compatibility is not guaranteed. Source comparison status is `{source_comparison['status']}`; disagreements require human review. Local evidence takes precedence over generic documentation.\n\n## Safety\n\nKnowledge from this profile is not execution permission. Any command must be classified and passed through the existing JARVIS safety and approval flow.\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"tool": name, "version": compatibility, "researched_at": researched_at, "sources": pages, "local_version": local, "project": project, "infrastructure": infrastructure, "source_comparison": source_comparison}, indent=2, default=str), encoding="utf-8")
    (path.parent / "commands.yaml").write_text("# Commands are references only; never execute without approval.\ncommands: []\n", encoding="utf-8")
    (path.parent / "troubleshooting.yaml").write_text("# Add verified failure signatures after local validation.\nissues: []\n", encoding="utf-8")
    (path.parent / "safety.yaml").write_text("execution: approval_required\nunknown_commands: do_not_execute\ndestructive_commands: explicit_approval\n", encoding="utf-8")
    (path.parent / "compatibility.yaml").write_text(f"requested_version: {version or 'unknown'}\ndetected_version: {local.get('version', 'unknown')}\nsource_comparison: {source_comparison['status']}\n", encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    logger.info("skill_created tool=%r path=%s", name, path)
    return {"success": True, "status": "created", "skill": str(path.relative_to(SKILLS_DIR.parent)), "version": version or None, "sources": pages}
