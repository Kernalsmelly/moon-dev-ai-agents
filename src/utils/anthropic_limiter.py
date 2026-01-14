"""Safe Anthropic wrapper to limit usage and avoid burning small API credits.

Features:
- Reads limits from environment variables (see README comments in .env).
- Enforces a rolling per-hour maximum request count.
- Enforces a max tokens per request value (best-effort; enforced by wrapper input checks).
- Supports a DRY_RUN mode which returns a harmless stub instead of performing a network call.
- Persists recent request timestamps to `.anthropic_usage.json` in repo root so limits survive restarts.

Usage:
    from src.utils.anthropic_limiter import call_anthropic
    resp = call_anthropic(prompt="Hello", model="claude-2.1", max_tokens=64)

Note: This wrapper will attempt to import the official `anthropic` SDK. If it's not installed
it will raise an informative ImportError so you can `pip install anthropic`.
"""
import os
import json
import time
import threading
from typing import Any, Dict, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
USAGE_FILE = os.path.join(ROOT, ".anthropic_usage.json")
_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


class AnthropicLimiter:
    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.max_requests_per_hour = int(os.getenv("ANTHROPIC_MAX_REQUESTS_PER_HOUR", "5"))
        self.max_tokens_per_request = int(os.getenv("ANTHROPIC_MAX_TOKENS_PER_REQUEST", "256"))
        self.dry_run = _env_bool("ANTHROPIC_DRY_RUN", False)
        self.usage_file = USAGE_FILE

    def _load_usage(self) -> Dict[str, Any]:
        try:
            with open(self.usage_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"timestamps": []}

    def _save_usage(self, data: Dict[str, Any]) -> None:
        try:
            with open(self.usage_file, "w") as f:
                json.dump(data, f)
        except Exception:
            # Non-fatal: best-effort persistence
            pass

    def _prune_old(self, timestamps: list[float]) -> list[float]:
        cutoff = time.time() - 3600
        return [t for t in timestamps if t >= cutoff]

    def can_send(self) -> bool:
        data = self._load_usage()
        ts = self._prune_old(data.get("timestamps", []))
        return len(ts) < self.max_requests_per_hour

    def record_request(self) -> None:
        with _lock:
            data = self._load_usage()
            ts = self._prune_old(data.get("timestamps", []))
            ts.append(time.time())
            data["timestamps"] = ts
            self._save_usage(data)

    def call_api(self, prompt: str, *, model: str = "claude-2", max_tokens: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        """Call Anthropic (or return stubbed response) enforcing configured limits.

        Args:
            prompt: The text prompt to send.
            model: Model name to request.
            max_tokens: Number of tokens to request (will be clamped to env limit if provided).
            **kwargs: Passed through to the underlying SDK call.

        Returns:
            A dict containing the provider response (or a stub when dry-run).
        """
        if max_tokens is not None and max_tokens > self.max_tokens_per_request:
            raise ValueError(f"Requested max_tokens={max_tokens} exceeds configured limit of {self.max_tokens_per_request}")

        if not self.can_send():
            raise RuntimeError("Anthropic request limit reached for the current 1-hour window")

        # Dry-run path: do not perform network calls, return a harmless stub
        if self.dry_run:
            # record the attempt so it counts toward limits even in dry-run mode
            self.record_request()
            return {
                "id": "dry-run",
                "model": model,
                "stub": True,
                "text": "[DRY RUN: Anthropic call suppressed by ANTHROPIC_DRY_RUN=true]",
            }

        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in environment (.env)")

        # perform the real call using the official SDK if available
        try:
            import anthropic  # type: ignore
        except Exception as e:
            raise ImportError("The 'anthropic' package is required to make real API calls. Install it with 'pip install anthropic'") from e

        client = anthropic.Client(api_key=self.api_key)

        # Prepare kwargs for the SDK - attempt to be compatible with common usage
        call_kwargs = dict(kwargs)
        if max_tokens is not None:
            # the Anthropics SDK parameter name varies; common param is `max_tokens_to_sample`
            call_kwargs.setdefault("max_tokens_to_sample", max_tokens)

        # Do the request and record usage on success
        # The exact call here uses the completions API common pattern
        resp = client.completions.create(model=model, prompt=prompt, **call_kwargs)
        self.record_request()
        # Convert SDK response to plain dict where possible
        try:
            return dict(resp)
        except Exception:
            return {"raw": resp}


# Module-level convenience helper
_limiter = AnthropicLimiter()


def call_anthropic(prompt: str, *, model: str = "claude-2", max_tokens: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Convenience wrapper around the AnthropicLimiter singleton."""
    return _limiter.call_api(prompt, model=model, max_tokens=max_tokens, **kwargs)
