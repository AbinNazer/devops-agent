from pathlib import Path

from app.project_intelligence import analyze_project, analyze_runtime_sources
from app.tool_registry import TOOL_NAMES, execute_tool


def test_project_analysis_is_grounded_and_redacts_secret_files(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\nimport os\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'ok': True}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_KEY=do-not-read\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
    result = analyze_project(tmp_path)
    assert result["success"] is True
    assert any(item["name"] == "FastAPI" for item in result["technologies"])
    assert result["apis"][0]["route"] == "/health"
    assert "SECRET_KEY" not in str(result)


def test_project_modes_and_registry(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"dependencies":{"react":"latest"}}', encoding="utf-8")
    monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
    assert "architecture" in analyze_project(tmp_path, "overview")
    assert "analyze_project" in TOOL_NAMES
    assert execute_tool("analyze_project", {"project_path": str(tmp_path)})["success"] is True


def test_runtime_sources_reads_env_but_redacts_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=postgres://user:secret@db/app\nPUBLIC_PORT=8000\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
    result = analyze_runtime_sources(tmp_path, include_logs=False)
    text = str(result)
    assert result["redacted"] is True
    assert "PUBLIC_PORT" in text
    assert "postgres://user:secret" not in text


def test_runtime_tool_registered():
    assert "analyze_runtime_sources" in TOOL_NAMES
