"""Redaction tests for the shared secret scrubber."""
from app.tool_factory.redaction import redact, redact_line


class TestRedact:
    def test_keyed_assignment_redacted_key_preserved(self):
        text = "db_password=hunter2\ntimeout=30"
        result = redact(text)
        assert "hunter2" not in result
        assert "db_password=" in result and "<redacted>" in result
        assert "timeout=30" in result

    def test_api_key_style(self):
        result = redact('api_key: "sk-abc123"')
        assert "sk-abc123" not in result
        assert "api_key" in result

    def test_pem_block_redacted(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\nplain"
        result = redact(text)
        assert "MIIabc" not in result
        assert "plain" in result

    def test_url_credentials_redacted(self):
        result = redact("connect to postgres://admin:s3cret@db:5432/app now")
        assert "s3cret" not in result
        assert "postgres://" in result

    def test_non_secret_content_untouched(self):
        assert redact("cpu 42%\ncontainer backend running") == "cpu 42%\ncontainer backend running"

    def test_byte_budget_enforced(self):
        big = ("x" * 50_000) + " password=leak"
        result = redact(big, max_bytes=1024)
        assert len(result.encode()) <= 1024 + len("\n...[truncated]")

    def test_empty_passthrough(self):
        assert redact("") == ""


class TestRedactLine:
    def test_line_clipped_and_redacted(self):
        line = "token=verysecretvalue " + "y" * 1000
        result = redact_line(line, max_length=100)
        assert len(result) <= 100
        assert "verysecretvalue" not in result
