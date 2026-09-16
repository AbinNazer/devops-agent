from unittest.mock import Mock, patch

from app.research import learn_tool, web_search
from app.tool_registry import TOOL_NAMES, execute_tool


def test_web_search_empty_query_is_safe():
    result = web_search("")
    assert result["success"] is False
    assert result["results"] == []


@patch("app.research.requests.get")
def test_web_search_returns_structured_results(mock_get):
    response = Mock()
    response.raise_for_status.return_value = None
    response.text = '<div class="result results_links results_links_deep web-result"><a class="result__a" href="https://docs.docker.com/test">Docker docs</a><a class="result__snippet">Official guide</a></div></div>'
    mock_get.return_value = response
    result = web_search("docker test")
    assert result["success"] is True
    assert result["results"][0]["url"] == "https://docs.docker.com/test"


@patch("app.research.fetch_documentation")
@patch("app.research.web_search")
def test_learn_tool_creates_profile(mock_search, mock_fetch, tmp_path, monkeypatch):
    import app.research as research
    monkeypatch.setattr(research, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(research, "RESEARCH_CACHE_DIR", tmp_path / "cache")
    mock_search.return_value = {"success": True, "results": [{"title": "Docs", "url": "https://example.com", "source": "example.com", "snippet": "Read-only notes"}]}
    mock_fetch.return_value = {"success": True, "title": "Docs", "content": "Documentation content"}
    result = learn_tool("Example Tool")
    assert result["success"] is True
    assert (tmp_path / "skills" / "example-tool" / "SKILL.md").exists()


def test_research_tools_are_registered():
    assert {"web_search", "learn_tool"}.issubset(TOOL_NAMES)
    assert execute_tool("web_search", {})["success"] is False
