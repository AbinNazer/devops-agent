"""
Regression tests for Phase 5 model-unavailable classification fix.

Ensures that model_not_found / model_unavailable errors correctly trigger
failover, even when they appear alongside error codes like "404" or status
messages like "bad_request".
"""
import pytest
from app.llm_router import _classify_error


class TestModelUnavailableClassification:
    """Model-unavailable errors must always be classified as 'failover'."""

    def test_model_not_found_basic(self):
        assert _classify_error("model_not_found") == "failover"

    def test_model_not_found_with_404(self):
        assert _classify_error("404 model_not_found") == "failover"

    def test_model_not_found_with_bad_request(self):
        assert _classify_error("bad_request: model 'xyz' not found") == "failover"

    def test_model_not_found_with_invalid(self):
        assert _classify_error("invalid_request: model_not_found") == "failover"

    def test_model_unavailable(self):
        assert _classify_error("model_unavailable") == "failover"

    def test_model_unavailable_with_404(self):
        assert _classify_error("404 model_unavailable") == "failover"

    def test_model_overloaded(self):
        assert _classify_error("model_overloaded") == "failover"

    def test_model_does_not_exist(self):
        assert _classify_error("model does not exist") == "failover"

    def test_model_access_unavailable(self):
        assert _classify_error("model access unavailable") == "failover"

    def test_model_not_found_groq_style(self):
        assert _classify_error(
            "groq.NotFoundError: 404 model_not_found: model 'xyz' does not exist"
        ) == "failover"

    def test_model_not_found_with_auth_contamination(self):
        """An error message containing BOTH model_not_found and 'auth' should
        still be classified as failover (model-unavailable takes priority)."""
        assert _classify_error(
            "auth failed: model_not_found: the model does not exist"
        ) == "failover"


class TestNonRetryableStillNonRetryable:
    """Auth/invalid errors must NOT be affected by the fix."""

    def test_auth_error(self):
        assert _classify_error("authentication_error: bad key") == "non_retryable"

    def test_invalid_api_key(self):
        assert _classify_error("invalid_api_key: wrong key") == "non_retryable"

    def test_unauthorized(self):
        assert _classify_error("unauthorized") == "non_retryable"

    def test_malformed_request(self):
        assert _classify_error("malformed request body") == "non_retryable"

    def test_bad_request_without_model(self):
        """A plain bad_request NOT about a model should be non-retryable."""
        assert _classify_error("bad_request: invalid parameter 'temperature'") == "non_retryable"


class TestFailoverErrorsUnchanged:
    """Rate limits and transient errors should still work."""

    def test_rate_limit(self):
        assert _classify_error("rate_limit_exceeded") == "failover"

    def test_timeout(self):
        assert _classify_error("connection_timeout") == "failover"

    def test_5xx(self):
        assert _classify_error("503 service unavailable") == "failover"

    def test_unknown(self):
        assert _classify_error("something weird happened") == "unknown"
