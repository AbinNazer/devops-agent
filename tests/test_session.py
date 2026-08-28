import os
import pytest
from app.session import save_session, load_session, list_sessions, SESSIONS_DIR


@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """Remove any test-created session files after each test."""
    yield
    for fname in ["test_session.json", "another_session.json"]:
        path = os.path.join(SESSIONS_DIR, fname)
        if os.path.exists(path):
            os.remove(path)


def test_save_session_creates_file():
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    path = save_session(history, "test_session")
    assert os.path.exists(path)


def test_load_session_returns_same_content():
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    save_session(history, "test_session")
    loaded = load_session("test_session")
    assert loaded == history


def test_save_session_without_name_uses_timestamp():
    history = [{"role": "system", "content": "sys"}]
    path = save_session(history)
    assert os.path.exists(path)
    os.remove(path)  # this one isn't covered by the fixture's fixed names


def test_load_missing_session_raises():
    with pytest.raises(FileNotFoundError):
        load_session("this_session_does_not_exist")


def test_list_sessions_includes_saved_ones():
    save_session([{"role": "system", "content": "sys"}], "test_session")
    save_session([{"role": "system", "content": "sys"}], "another_session")
    sessions = list_sessions()
    assert "test_session" in sessions
    assert "another_session" in sessions


def test_save_session_name_without_json_extension_still_works():
    history = [{"role": "user", "content": "x"}]
    path = save_session(history, "test_session")  # no .json suffix given
    assert path.endswith("test_session.json")
