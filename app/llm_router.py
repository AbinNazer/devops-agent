"""
Multi-Provider LLM Router with Automatic Failover.

Wraps multiple LLMProvider instances and transparently fails over when
the primary (or any fallback) provider hits rate limits, timeouts,
temporary errors, or becomes unavailable.

Architecture:
    Agent
      ↓
    LLMRouter
      ├── Provider 1 (primary)
      ├── Provider 2 (fallback)
      ├── Provider 3 (fallback)
      └── Provider 4 (fallback)

Failover triggers:
    - HTTP 429 (rate limit)
    - Connection timeout
    - Network error
    - Temporary server error (5xx)
    - Model unavailable
    - Provider temporarily unavailable

Non-trigger errors (no failover):
    - Malformed request
    - Invalid tool schema
    - Authentication failure (permanent)

The router preserves full conversation context across provider switches.
The rest of JARVIS (Agent, tools, security, Phase 5/6) is unchanged.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Any

from app.llm_provider import (
    LLMProvider, GroqProvider, OllamaProvider, OpenAIProvider, AnthropicProvider,GeminiProvider
)

logger = logging.getLogger("llm_router")

# --- Error classification ---

# Errors that should trigger failover to the next provider
FAILOVER_ERRORS = (
    "rate_limit", "rate_limit_exceeded", "tokens_per_minute",
    "requests_per_minute", "quota_exceeded", "429",
    "timeout", "connection_error", "connection_timeout", "connect_timeout",
    "server_error", "500", "502", "503", "504",
    "model_not_found", "model_unavailable", "model_overloaded",
    "provider_unavailable", "temporarily_unavailable",
    "overloaded", "capacity",
)

# Errors that should NOT trigger failover (the same provider should not help)
NON_RETRYABLE_ERRORS = (
    "authentication", "auth", "invalid_api_key", "unauthorized",
    "invalid_request", "bad_request", "malformed",
)

# Model-unavailable patterns: check these FIRST so that a message like
# "bad_request: model 'xyz' not found" or "404: model not available"
# correctly triggers failover instead of being caught by the generic
# "bad_request" / "invalid" substring match below.
MODEL_UNAVAILABLE_PATTERNS = (
    "model_not_found", "model_unavailable", "model_overloaded",
    "model not found", "model unavailable",
    "model does not exist", "does not exist",
    "model access unavailable",
)
# Regex patterns for model-unavailable errors with variable model names
# e.g. "bad_request: model 'xyz' not found"
MODEL_UNAVAILABLE_REGEX = (r"model.*not found", r"model.*does not exist")

# Provider status constants
STATUS_AVAILABLE = "available"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"
STATUS_COOLDOWN = "cooldown"

# Default cooldown after a provider fails (seconds)
DEFAULT_COOLDOWN_SECONDS = 30
MAX_COOLDOWN_SECONDS = 300  # 5 minutes max cooldown


def _classify_error(error_msg: str) -> str:
    """Classify an error message into a category.

    Priority:
    1. Model-unavailable patterns (retryable → failover to next provider)
    2. Rate limits / transient errors (retryable → failover)
    3. Auth / invalid request (non-retryable → no failover)
    4. Unknown → no failover
    """
    import re
    msg_lower = error_msg.lower()

    # 1) Model-unavailable: always retryable (failover)
    for pattern in MODEL_UNAVAILABLE_PATTERNS:
        if pattern in msg_lower:
            return "failover"
    for regex in MODEL_UNAVAILABLE_REGEX:
        if re.search(regex, msg_lower):
            return "failover"

    # 2) Rate limits / transient errors: retryable
    for pattern in FAILOVER_ERRORS:
        if pattern in msg_lower:
            return "failover"

    # 3) Auth / invalid request: non-retryable
    for pattern in NON_RETRYABLE_ERRORS:
        if pattern in msg_lower:
            return "non_retryable"

    return "unknown"



CORE_TOOLS_FOR_SMALL_MODELS = {
    "check_infrastructure_health",
    "docker_status",
    "get_cpu_usage",
    "get_memory_usage",
    "get_disk_usage",
    "get_uptime",
}

class ProviderState:
    """Tracks the state of a single provider for routing decisions."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.status = STATUS_AVAILABLE
        self.failures = 0
        self.cooldown_until = 0.0
        self.last_error = ""
        self.total_requests = 0
        self.total_failures = 0
        self.total_tokens = 0
        self.last_request_time = 0.0

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def is_cooled_down(self) -> bool:
        return time.time() >= self.cooldown_until

    @property
    def is_usable(self) -> bool:
        return self.status != STATUS_FAILED and self.is_cooled_down

    def record_success(self) -> None:
        """Record a successful request — reset failure state."""
        self.failures = 0
        self.status = STATUS_AVAILABLE
        self.cooldown_until = 0.0
        self.total_requests += 1
        self.last_request_time = time.time()

    def record_failure(self, error_msg: str, retryable: bool = True) -> None:
        """Record a failed request — apply cooldown if retryable."""
        self.failures += 1
        self.total_failures += 1
        self.last_error = error_msg
        self.total_requests += 1
        self.last_request_time = time.time()

        if retryable:
            # Exponential cooldown: 30s, 60s, 120s, capped at 5 min
            cooldown = min(DEFAULT_COOLDOWN_SECONDS * (2 ** (self.failures - 1)),
                          MAX_COOLDOWN_SECONDS)
            self.cooldown_until = time.time() + cooldown
            self.status = STATUS_COOLDOWN
        else:
            self.status = STATUS_FAILED

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "failures": self.failures,
            "cooldown_remaining": max(0, round(self.cooldown_until - time.time(), 1)),
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "last_error": self.last_error,
        }


class LLMRouter(LLMProvider):
    """
    Multi-provider LLM router with automatic failover.

    Implements the LLMProvider interface so the Agent uses it exactly
    like a single provider — no changes needed in agent.py.

    Usage:
        router = LLMRouter(providers=[groq, openai, anthropic, ollama])
        result = router.chat(messages, tools)

    Or via build_router(config):
        router = build_router(config)
    """

    def __init__(self, providers: List[LLMProvider], name: Optional[str] = None):
        if not providers:
            raise ValueError("LLMRouter requires at least one provider")
        self._states = [ProviderState(p) for p in providers]
        self._primary_index = 0
        self.name = name or f"LLMRouter ({', '.join(s.name for s in self._states)})"

    @property
    def providers(self) -> List[ProviderState]:
        return list(self._states)

    @property
    def primary(self) -> ProviderState:
        return self._states[self._primary_index]

    def check_alive(self) -> None:
        """Check that at least one provider is reachable."""
        last_error = None
        for state in self._states:
            try:
                state.provider.check_alive()
                return
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(
            f"No LLM provider is reachable. Last error: {last_error}"
        )

    def chat(self, messages: list, tools: list) -> dict:
        """
        Send a chat request with automatic failover.

        Tries providers in order: primary → fallbacks.
        On failure, classifies the error and either retries the same
        provider (if the error is transient) or fails over to the next one.
        """
        errors = []
        tried_indices = set()

        def call_provider(provider_idx: int):
            state = self._states[provider_idx]
            effective_tools = tools
            if isinstance(state.provider, OllamaProvider):
                effective_tools = [
                    t for t in tools
                    if t["function"]["name"] in CORE_TOOLS_FOR_SMALL_MODELS
                ]
            return state.provider.chat(messages, effective_tools)

        def record_failure(provider_idx: int, error: Exception) -> None:
            state = self._states[provider_idx]
            error_msg = str(error)
            retryable = _classify_error(error_msg) != "non_retryable"
            state.record_failure(error_msg, retryable=retryable)
            logger.warning(
                "llm_provider_failed provider=%s error=%s failing_over=True",
                state.name, error_msg[:200],
            )
            errors.append(f"{state.name}: {error_msg[:100]}")

        # Keep the preferred provider as a single first attempt. This avoids
        # unnecessary duplicate requests when it is healthy.
        primary_idx = self._find_next_provider(tried_indices)
        if primary_idx is not None:
            tried_indices.add(primary_idx)
            state = self._states[primary_idx]
            logger.info("llm_request provider=%s attempt=1/%d", state.name, len(self._states))
            try:
                result = call_provider(primary_idx)
                state.record_success()
                return result
            except Exception as error:
                record_failure(primary_idx, error)

        # Once the primary fails, fallback providers are independent network
        # calls. Race them so a slow/dead provider cannot delay a healthy one.
        fallback_indices = []
        while True:
            provider_idx = self._find_next_provider(tried_indices)
            if provider_idx is None:
                break
            tried_indices.add(provider_idx)
            fallback_indices.append(provider_idx)

        if fallback_indices:
            workers = min(len(fallback_indices), 4)
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="llm-fallback")
            futures = {
                pool.submit(call_provider, idx): idx for idx in fallback_indices
            }
            try:
                for future in as_completed(futures):
                    provider_idx = futures[future]
                    state = self._states[provider_idx]
                    logger.info("llm_fallback_race provider=%s", state.name)
                    try:
                        result = future.result()
                        state.record_success()
                        logger.info("llm_request_succeeded_after_failover provider=%s", state.name)
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()
                        return result
                    except Exception as error:
                        record_failure(provider_idx, error)
            finally:
                # Do not wait for a timed-out provider after another fallback
                # has already failed/succeeded. Its worker is isolated and the
                # provider state will record the eventual failure if it returns.
                pool.shutdown(wait=False, cancel_futures=True)

        # All providers exhausted
        error_summary = "; ".join(errors) if errors else "No providers available"
        raise RuntimeError(
            f"All configured LLM providers are currently unavailable. {error_summary}"
        )

    def _find_next_provider(self, exclude: set) -> Optional[int]:
        """Find the next usable provider index, starting from primary."""
        for i in range(len(self._states)):
            idx = (self._primary_index + i) % len(self._states)
            if idx in exclude:
                continue
            state = self._states[idx]
            if state.is_usable:
                return idx
        return None

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        """Delegate to the primary provider's message format."""
        return self._states[self._primary_index].provider.assistant_message(
            content, tool_calls,
        )

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        """Delegate to the primary provider's message format."""
        return self._states[self._primary_index].provider.tool_result_message(
            tool_call, result,
        )

    def get_status(self) -> Dict[str, Any]:
        """Get the status of all providers."""
        return {
            "primary": self._states[self._primary_index].name,
            "providers": [s.to_dict() for s in self._states],
        }


def build_router(config) -> LLMRouter:
    """
    Build an LLM router from configuration.

    The primary provider is determined by config.LLM_PROVIDER.
    Fallback providers are from config.LLM_FALLBACK_PROVIDERS.

    If no fallbacks are configured, returns a single-provider router
    (backward compatible — behaves exactly like the old build_provider).
    """
    def _make_provider(name: str) -> Optional[LLMProvider]:
        """Create a provider by name, returning None if not configured."""
        if name == "groq":
            if not config.GROQ_API_KEY:
                return None
            return GroqProvider(api_key=config.GROQ_API_KEY, model=config.GROQ_MODEL)
        if name == "openai":
            if not config.OPENAI_API_KEY:
                return None
            return OpenAIProvider(
                api_key=config.OPENAI_API_KEY, model=config.OPENAI_MODEL,
                base_url=getattr(config, "OPENAI_BASE_URL", ""),
            )
        if name == "openrouter":
            if not config.OPENROUTER_API_KEY:
                return None
            return OpenAIProvider(
                api_key=config.OPENROUTER_API_KEY, model=config.OPENROUTER_MODEL,
                base_url="https://openrouter.ai/api/v1", label="OpenRouter",
            )
        if name == "nvidia":
            if not config.NVIDIA_NIM_API_KEY:
                return None
            return OpenAIProvider(
                api_key=config.NVIDIA_NIM_API_KEY, model=config.NVIDIA_NIM_MODEL,
                base_url="https://integrate.api.nvidia.com/v1", label="NVIDIA NIM",
            )
        if name == "anthropic":
            if not config.ANTHROPIC_API_KEY:
                return None
            return AnthropicProvider(api_key=config.ANTHROPIC_API_KEY, model=config.ANTHROPIC_MODEL)

        if name == "gemini":
            if not config.GEMINI_API_KEY:
                return None
            return GeminiProvider(
                 api_key=config.GEMINI_API_KEY,
                 model=config.GEMINI_MODEL,
               ) 


        if name == "ollama":
            return OllamaProvider(host=config.OLLAMA_HOST, model=config.OLLAMA_MODEL)
        return None

    # Build provider list: primary first, then fallbacks
    provider_names = [config.LLM_PROVIDER] + list(config.LLM_FALLBACK_PROVIDERS)
    providers = []
    for name in provider_names:
        p = _make_provider(name)
        if p is not None:
            providers.append(p)

    if not providers:
        raise RuntimeError("No LLM providers could be configured. Check your .env settings.")

    # If only one provider, return it directly (no router overhead)
    if len(providers) == 1:
        return LLMRouter(providers=providers, name=providers[0].name)

    return LLMRouter(providers=providers)
