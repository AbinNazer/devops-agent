from app.config import Config, validate_vps_config


def test_validate_vps_config_reports_missing_host(monkeypatch):
    monkeypatch.setattr(Config, "VPS_HOST", "")
    monkeypatch.setattr(Config, "VPS_SSH_USER", "root")
    monkeypatch.setattr(Config, "VPS_SSH_KEY_PATH", "")
    problems = validate_vps_config()
    assert any("VPS_HOST" in p for p in problems)


def test_validate_vps_config_reports_missing_user(monkeypatch):
    monkeypatch.setattr(Config, "VPS_HOST", "1.2.3.4")
    monkeypatch.setattr(Config, "VPS_SSH_USER", "")
    monkeypatch.setattr(Config, "VPS_SSH_KEY_PATH", "")
    problems = validate_vps_config()
    assert any("VPS_SSH_USER" in p for p in problems)


def test_validate_vps_config_reports_missing_key_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "VPS_HOST", "1.2.3.4")
    monkeypatch.setattr(Config, "VPS_SSH_USER", "root")
    monkeypatch.setattr(Config, "VPS_SSH_KEY_PATH", str(tmp_path / "does_not_exist"))
    problems = validate_vps_config()
    assert any("SSH key not found" in p for p in problems)


def test_validate_vps_config_passes_when_everything_present(monkeypatch, tmp_path):
    key_file = tmp_path / "fake_key"
    key_file.write_text("fake")
    monkeypatch.setattr(Config, "VPS_HOST", "1.2.3.4")
    monkeypatch.setattr(Config, "VPS_SSH_USER", "root")
    monkeypatch.setattr(Config, "VPS_SSH_KEY_PATH", str(key_file))
    assert validate_vps_config() == []