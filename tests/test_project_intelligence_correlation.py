"""Correlation tests for project intelligence."""
from pathlib import Path

from app.project_intelligence import correlate


def _write_project(tmp_path: Path) -> Path:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  backend:\n    image: erp/backend\n    depends_on:\n      - redis\n  redis:\n    image: redis:7\n",
        encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import redis\n# backend connects to redis for caching\nclient = redis.Redis(host='redis')\n",
        encoding="utf-8")
    (tmp_path / "worker.py").write_text(
        "def handle_error():\n    raise RuntimeError('connection refused to redis')\n",
        encoding="utf-8")
    return tmp_path


class TestCorrelate:
    def test_requires_input(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
        result = correlate(project_path=tmp_path)
        assert result["success"] is False

    def test_container_linked_to_compose_service(self, tmp_path, monkeypatch):
        _write_project(tmp_path)
        monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
        result = correlate(container_name="backend", project_path=tmp_path)
        assert result["success"] is True
        links = {link["container"]: link for link in result["container_links"]}
        assert links["backend"]["matched_as"] == "compose_service"
        assert links["backend"]["confidence"] >= 0.9
        assert result["compose_files"] == ["docker-compose.yml"]

    def test_error_text_matches_source(self, tmp_path, monkeypatch):
        _write_project(tmp_path)
        monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
        result = correlate(error_message="connection refused to redis",
                           project_path=tmp_path)
        files = {match["file"] for match in result["source_matches"]}
        assert "worker.py" in files
        assert result["confidence"] > 0

    def test_hinted_containers_extracted_from_error(self, tmp_path, monkeypatch):
        _write_project(tmp_path)
        monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
        result = correlate(error_message='container "backend" exited with code 1',
                           project_path=tmp_path)
        assert "backend" in result["hinted_containers"]

    def test_unknown_container_marked_unlinked(self, tmp_path, monkeypatch):
        _write_project(tmp_path)
        monkeypatch.setenv("JARVIS_PROJECT_ROOTS", str(tmp_path))
        result = correlate(container_name="mystery_box", project_path=tmp_path)
        links = {link["container"]: link for link in result["container_links"]}
        assert links["mystery_box"]["matched_as"] == "unlinked"

    def test_path_outside_roots_refused(self, tmp_path):
        # No JARVIS_PROJECT_ROOTS and cwd is not tmp_path -> refused.
        result = correlate(container_name="backend", project_path=tmp_path)
        assert result["success"] is False
