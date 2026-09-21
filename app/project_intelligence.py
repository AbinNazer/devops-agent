"""Bounded, read-only project code intelligence.

The analyzer reports evidence from files; it does not execute project code or
expose secret values.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

IGNORED = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "coverage", ".mypy_cache", ".pytest_cache"}
SECRET_FILE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
TEXT_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".vue", ".html", ".css", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".md", ".sql", ".sh", ".xml", ".properties"}
RUNTIME_ROOTS = (Path("/etc/nginx"), Path("/etc/cron.d"), Path("/etc/cron.hourly"), Path("/etc/cron.daily"), Path("/etc/cron.weekly"), Path("/etc/cron.monthly"), Path("/var/log"), Path("/var/spool/cron"))
SECRET_KEY_PATTERN = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|authorization|credential|database[_-]?url|dsn)\s*([:=])\s*([^\s#]+)")


def _redact_runtime(text: str, limit: int = 12000) -> str:
    text = SECRET_KEY_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    text = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "<private-key-redacted>", text, flags=re.S)
    return text[-limit:]


def _runtime_file_allowed(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    return any(resolved == root or root in resolved.parents for root in RUNTIME_ROOTS)


def analyze_runtime_sources(project_path: str | Path = ".", include_logs: bool = True, max_files: int = 60) -> dict:
    """Read approved runtime config/log locations with values redacted."""
    project = _safe_path(project_path)
    candidates = [path for path in project.glob(".env*") if path.is_file()]
    if Path("/etc/crontab").is_file():
        candidates.append(Path("/etc/crontab"))
    candidates += [path for path in project.rglob("*.conf") if path.is_file() and any(part in {"nginx", "conf", "config"} for part in path.parts)][:20]
    candidates += [path for root in RUNTIME_ROOTS[:6] if root.exists() for path in root.rglob("*") if path.is_file()]
    if include_logs:
        candidates += [path for path in RUNTIME_ROOTS[-2:] if path.exists() for path in path.rglob("*.log") if path.is_file()]
    seen, files = set(), []
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved in seen or len(files) >= max_files or (resolved not in [p.resolve() for p in project.glob(".env*")] and not _runtime_file_allowed(resolved)):
                continue
            if path.stat().st_size > 2_000_000:
                continue
            seen.add(resolved)
            files.append({"path": str(path), "kind": "log" if ".log" in path.name else "config", "content": _redact_runtime(_read(path))})
        except OSError:
            continue
    return {"success": True, "project": str(project), "files": files, "redacted": True, "note": "Values matching secret patterns are redacted before analysis."}


def _safe_path(project_path: str | Path) -> Path:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("project_path must be an existing directory")
    allowed = [Path.cwd().resolve()]
    allowed.extend(Path(item).expanduser().resolve() for item in os.getenv("JARVIS_PROJECT_ROOTS", "").split(os.pathsep) if item)
    if not any(root == candidate or candidate in root.parents for candidate in allowed):
        raise PermissionError("project path is outside the authorized project roots")
    return root


def _files(root: Path, limit: int = 5000) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if len(found) >= limit or not path.is_file() or any(part in IGNORED for part in path.relative_to(root).parts):
            continue
        if path.name in SECRET_FILE_NAMES or path.suffix.lower() in {".db", ".sqlite", ".pem", ".key", ".p12"}:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        found.append(path)
    return found


def list_project_tree(project_path: str | Path = ".", query: str = "", max_entries: int = 200) -> dict:
    """Return a fast, bounded directory tree for an authorized project.

    This is intentionally metadata-only: it never reads file contents and
    skips virtual environments, dependency folders, caches, and secrets.
    """
    root = _safe_path(project_path)
    needle = query.strip().lower()
    entries = []
    pending = [root]
    while pending and len(entries) < max_entries:
        current = pending.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            continue
        for path in children:
            relative = path.relative_to(root)
            if any(part in IGNORED for part in relative.parts) or path.name in SECRET_FILE_NAMES:
                continue
            if needle and needle not in str(relative).lower():
                if path.is_dir():
                    pending.append(path)
                continue
            entries.append({"path": str(relative), "name": path.name, "type": "directory" if path.is_dir() else "file"})
            if path.is_dir() and len(entries) < max_entries:
                pending.append(path)
            if len(entries) >= max_entries:
                break
    return {"success": True, "project": str(root), "entries": entries, "truncated": bool(pending), "max_entries": max_entries}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:200_000]
    except OSError:
        return ""


def _classify(path: Path, text: str) -> str:
    name = path.name.lower()
    if name in {"dockerfile", ".dockerignore"} or "docker-compose" in name: return "Docker"
    if "/" in str(path) and any(part in {"k8s", "kubernetes", "helm", "charts", "manifests"} for part in path.parts): return "Infrastructure"
    if name in {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "composer.json"}: return "Dependencies"
    if "test" in name or "/tests/" in str(path) or "/test/" in str(path): return "Tests"
    if name.endswith((".yaml", ".yml", ".toml", ".ini", ".cfg")) or name.startswith("config") or "settings" in name: return "Configuration"
    if name in {"readme.md", "license", "changelog.md"}: return "Documentation"
    if re.search(r"@(app|router)\.(get|post|put|patch|delete|websocket)|urls?\s*=|router\.(get|post)", text): return "API"
    if re.search(r"jwt|oauth|csrf|permission|authenticate|authorization|session", text, re.I): return "Authentication/Security"
    return "Application code" if path.suffix.lower() in TEXT_EXTENSIONS else "Other"


def _detect_technologies(root: Path, texts: dict[Path, str]) -> list[dict]:
    evidence = []
    checks = [("Python", {".py", "requirements.txt", "pyproject.toml"}, r"\bpython\b|fastapi|django|flask"), ("FastAPI", set(), r"from fastapi|FastAPI\("), ("Django", set(), r"django|manage\.py"), ("Node.js", {"package.json", ".js", ".ts"}, r"express|next|nestjs|node"), ("React", set(), r"from ['\"]react['\"]|react-dom"), ("Docker", set(), r"FROM\s+|docker-compose|container_name"), ("Kubernetes", set(), r"apiVersion:\s|kind:\s+(Deployment|Service|Ingress)"), ("PostgreSQL", set(), r"postgres|psycopg|PostgreSQL"), ("Redis", set(), r"redis|ioredis"), ("Celery", set(), r"celery|@shared_task"), ("WebSockets", set(), r"WebSocket|websocket|socket\.io")]
    for name, extensions, pattern in checks:
        matched = [str(path.relative_to(root)) for path, text in texts.items() if (not extensions or path.name in extensions or path.suffix in extensions) and re.search(pattern, text, re.I)]
        if matched:
            evidence.append({"name": name, "evidence_files": matched[:12]})
    return evidence


def analyze_project(project_path: str | Path = ".", mode: str = "full", specific_module: str = "") -> dict:
    root = _safe_path(project_path)
    files = _files(root)
    texts = {path: _read(path) for path in files}
    classifications = defaultdict(list)
    for path, text in texts.items():
        classifications[_classify(path, text)].append(str(path.relative_to(root)))
    dependencies = []
    for path, text in texts.items():
        source = str(path.relative_to(root))
        for match in re.findall(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", text, re.M):
            if not match.startswith(("os", "sys", "typing", "json", "re", "pathlib", "datetime")):
                dependencies.append({"source": source, "target": match.split(".")[0], "relationship": "imports"})
    entrypoints = []
    for path, text in texts.items():
        if path.name in {"manage.py", "main.py", "app.py", "server.py", "index.js", "server.js", "main.go", "asgi.py", "wsgi.py"} or re.search(r"if __name__ == ['\"]__main__['\"]|uvicorn\.run|app\.run\(", text):
            entrypoints.append({"file": str(path.relative_to(root)), "evidence": "entrypoint name or executable guard"})
    routes = []
    for path, text in texts.items():
        for match in re.finditer(r"@(?:app|router)\.(get|post|put|patch|delete|websocket)\(\s*['\"]([^'\"]+)", text):
            routes.append({"method": match.group(1).upper(), "route": match.group(2), "file": str(path.relative_to(root))})
    symbols = []
    for path, text in texts.items():
        for match in re.finditer(r"^\s*(?:async\s+)?(?:def|class|function)\s+([A-Za-z_$][\w$]*)", text, re.M):
            symbols.append({"name": match.group(1), "file": str(path.relative_to(root))})
    env_names = set()
    for text in texts.values():
        env_names.update(re.findall(r"(?:os\.environ\.get|os\.getenv)\(\s*['\"]([A-Z][A-Z0-9_]+)", text))
        env_names.update(re.findall(r"\$\{?([A-Z][A-Z0-9_]+)\}?", text))
    result = {"success": True, "project": {"name": root.name, "path": str(root)}, "technologies": _detect_technologies(root, texts), "file_count": len(files), "classifications": dict(classifications), "entrypoints": entrypoints[:100], "dependencies": dependencies[:500], "apis": routes[:300], "symbols": symbols[:500], "configuration_variables": sorted(env_names), "deployment_files": [str(path.relative_to(root)) for path in files if path.name.lower() in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "jenkinsfile"} or path.suffix in {".yaml", ".yml"} and any(part in {"k8s", "kubernetes", "helm", "deploy"} for part in path.parts)], "architecture": {"mermaid": _mermaid(_detect_technologies(root, texts), routes)}}
    if specific_module:
        result["specific_module"] = [item for item in result["symbols"] if specific_module.lower() in item["file"].lower() or specific_module.lower() in item["name"].lower()]
    if mode in {"overview", "architecture"}:
        return {key: result[key] for key in ("success", "project", "technologies", "file_count", "classifications", "entrypoints", "architecture")}
    if mode == "api": return {key: result[key] for key in ("success", "project", "apis", "configuration_variables")}
    if mode == "dependencies": return {key: result[key] for key in ("success", "project", "technologies", "dependencies")}
    return result


# ── Correlation: errors ↔ containers ↔ projects ─────────────────────

_CONTAINER_HINT_RE = re.compile(
    r"(?:container|service)\s+[\"']?([a-z0-9][\w.\-]{1,63})[\"']?", re.I)


def correlate(error_message: str = "", container_name: str = "", project_path: str | Path = ".",
              max_files: int = 40) -> dict:
    """Correlate an error message with containers, services, projects, and source files.

    Read-only and evidence-based: every returned link carries the file path
    that justified it and a confidence score derived from the strength of the
    match. Never executes project code; never exposes secret values.
    """
    error_message = (error_message or "").strip()
    container_name = (container_name or "").strip()
    if not error_message and not container_name:
        return {"success": False, "error": "provide an error_message or container_name"}

    evidence: list[dict] = []
    # Container hints inside the error text itself.
    hinted_containers = sorted({match.group(1).lower() for match in _CONTAINER_HINT_RE.finditer(error_message)})

    # Compose/service detection from deployment files in the project.
    compose_services: list[str] = []
    compose_files: list[str] = []
    try:
        root = _safe_path(project_path)
    except (ValueError, PermissionError) as exc:
        return {"success": False, "error": str(exc)}
    for path in _files(root, limit=400):
        name = path.name.lower()
        if "docker-compose" in name or name == "compose.yaml":
            text = _read(path)
            compose_files.append(str(path.relative_to(root)))
            for service_match in re.finditer(r"^\s{2}([\w\-]+):\s*$", text, re.M):
                compose_services.append(service_match.group(1).lower())
    compose_services = sorted(set(compose_services))

    # Source files that reference the container/service or reproduce the error string.
    needle = (container_name or (hinted_containers[0] if hinted_containers else "")).lower()
    compact_needle = "".join(ch for ch in needle if ch.isalnum())
    source_matches: list[dict] = []
    error_snippet = re.sub(r"\s+", " ", error_message)[:80].strip()
    if error_snippet:
        error_snippet = re.escape(error_snippet[:40])
    for path in _files(root, limit=600):
        text = _read(path)
        relative = str(path.relative_to(root))
        score = 0.0
        reasons = []
        if needle and (needle in text.lower() or (compact_needle and compact_needle in "".join(ch for ch in text.lower() if ch.isalnum()))):
            score += 0.5
            reasons.append(f"references '{needle}'")
        if error_snippet and re.search(error_snippet, text, re.I):
            score += 0.4
            reasons.append("contains matching error text")
        if score > 0:
            source_matches.append({"file": relative, "confidence": round(min(score, 0.9), 2), "evidence": reasons})
        if len(source_matches) >= max_files:
            break
    source_matches.sort(key=lambda item: -item["confidence"])

    # Container ↔ compose-service correlation with confidence.
    container_links = []
    candidates = ([container_name.lower()] if container_name else []) + hinted_containers
    for candidate in set(candidates):
        if candidate in compose_services:
            container_links.append({"container": candidate, "matched_as": "compose_service",
                                    "confidence": 0.9, "evidence": compose_files})
        elif any(candidate in service or service in candidate for service in compose_services):
            container_links.append({"container": candidate, "matched_as": "fuzzy_compose_service",
                                    "confidence": 0.5, "evidence": compose_files})
        else:
            container_links.append({"container": candidate, "matched_as": "unlinked",
                                    "confidence": 0.1, "evidence": []})

    overall = 0.0
    if container_links:
        overall = max(overall, max(link["confidence"] for link in container_links))
    if source_matches:
        overall = max(overall, source_matches[0]["confidence"])

    return {
        "success": True,
        "error_message": error_message[:500],
        "container_name": container_name,
        "hinted_containers": hinted_containers,
        "compose_services": compose_services,
        "compose_files": compose_files,
        "container_links": container_links,
        "source_matches": source_matches[:20],
        "confidence": round(overall, 2),
        "note": "Confidence reflects evidence strength only; current live evidence always outranks these links.",
    }


def _mermaid(technologies: list[dict], routes: list[dict]) -> str:
    nodes = [item["name"].replace("-", "_") for item in technologies[:10]]
    lines = ["flowchart TD"] + [f"  {node.replace(' ', '_')}[{node}]" for node in nodes]
    if len(nodes) > 1:
        lines.extend(f"  {nodes[index]} --> {nodes[index + 1]}" for index in range(len(nodes) - 1))
    if routes and nodes: lines.append(f"  API --> {nodes[0]}")
    return "\n".join(lines)
