"""Research progress / disagreement / compatibility / memory tests."""
import json

from app import research


def _seed_skill(slug, source_status="needs_review", local_version="7.2.4"):
    research.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    research.RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    skill_dir = research.SKILLS_DIR / slug
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {slug}\n", encoding="utf-8")
    (research.RESEARCH_CACHE_DIR / f"{slug}.json").write_text(json.dumps({
        "tool": slug, "version": "7.2", "researched_at": "2026-09-21T00:00:00+00:00",
        "sources": [{"title": "Docs", "url": "https://docs.example.com"}],
        "local_version": {"status": "detected", "version": local_version},
        "project": {"status": "found", "matches": []},
        "source_comparison": {"status": source_status, "sources_compared": 2, "conflicts": []},
    }), encoding="utf-8")


class TestProgress:
    def test_lists_seeded_skill(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        _seed_skill("redis")
        result = research.list_research_progress()
        assert result["success"] and result["count"] == 1
        skill = result["skills"][0]
        assert skill["skill"] == "redis"
        assert skill["detected_version"] == "7.2.4"

    def test_empty_when_no_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        result = research.list_research_progress()
        assert result["count"] == 0


class TestDisagreements:
    def test_flags_needs_review(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        _seed_skill("docker", source_status="needs_review")
        _seed_skill("helm", source_status="agreeing")
        result = research.source_disagreement_report()
        names = {item["skill"] for item in result["disagreements"]}
        assert "docker" in names and "helm" not in names


class TestCompatibility:
    def test_report_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        _seed_skill("prometheus")
        result = research.project_compatibility_report("prometheus")
        assert result["success"] is True
        assert result["detected_version"] == "7.2.4"
        assert result["requested_version"] == "7.2"

    def test_unknown_tool_fails_cleanly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        result = research.project_compatibility_report("never_researched")
        assert result["success"] is False


class TestMemoryIntegration:
    def test_remember_skill_stores_semantic_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        _seed_skill("grafana")

        stored = {}

        class FakeRepo:
            def store_memory(self, memory):
                stored["memory"] = memory

        result = research.remember_skill("grafana", memory_repository=FakeRepo())
        assert result["success"] is True
        memory = stored["memory"]
        assert memory.type == "semantic"
        assert "approval" in memory.content.lower() or "reference" in memory.content.lower()

    def test_remember_skill_without_profile_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "skills" / ".cache")
        result = research.remember_skill("nothing", memory_repository=None)
        assert result["success"] is False
