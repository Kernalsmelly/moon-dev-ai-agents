from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests


def _split_csv(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]

def _expand_env_ref(v: str) -> str:
    """Resolve shell-style indirection like $FOO or ${FOO} inside env-driven config."""
    s = (v or "").strip()
    if s.startswith("${") and s.endswith("}") and len(s) > 3:
        s = s[2:-1]
    if s.startswith("$") and len(s) > 1:
        s = os.getenv(s[1:], v).strip()
    return s.strip()


def _load_pool_file(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    urls: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                u = item.get("url")
                # Require getLatestBlockhash success; some providers allow getHealth
                # but paywall/deny other RPC methods.
                ok = bool(item.get("success_getLatestBlockhash"))
                if ok and isinstance(u, str) and u.startswith(("http://", "https://")):
                    urls.append(u)
    elif isinstance(data, dict):
        # allow {"pool":[{"url":...}, ...]}
        pool = data.get("pool")
        if isinstance(pool, list):
            for item in pool:
                if isinstance(item, dict):
                    u = item.get("url")
                    ok = bool(item.get("success_getLatestBlockhash"))
                    if ok and isinstance(u, str) and u.startswith(("http://", "https://")):
                        urls.append(u)
    # de-dupe while preserving order
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def default_rpc_urls() -> list[str]:
    # Priority:
    # 1) explicit RPC_URLS (comma-separated)
    # 2) RPC_POOL_FILE (JSON produced by scripts/key_health_check.py)
    # 3) RPC_URL / RPC_ENDPOINT
    urls = [_expand_env_ref(u) for u in _split_csv(os.getenv("RPC_URLS", ""))]
    urls = [u for u in urls if u.startswith(("http://", "https://"))]
    if urls:
        return urls
    pool_file = os.getenv("RPC_POOL_FILE", "").strip()
    if pool_file and os.path.exists(pool_file):
        urls = _load_pool_file(pool_file)
        if urls:
            return urls
    single = _expand_env_ref((os.getenv("RPC_URL") or os.getenv("RPC_ENDPOINT") or "").strip())
    return [single] if single else []


@dataclass
class RpcError(RuntimeError):
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


class RpcPool:
    """Tiny RPC client with endpoint rotation + cooldown on rate limits.

    This is intentionally simple: we want to keep the meme pipeline running even when
    free tiers throttle. Callers should treat RpcError(kind='rate_limited') as a
    signal to slow down.
    """

    def __init__(
        self,
        urls: Iterable[str] | None = None,
        *,
        timeout_s: float = 12.0,
        max_attempts: int = 3,
    ) -> None:
        self.urls = [u.strip() for u in (urls or default_rpc_urls()) if u and u.strip()]
        self.timeout_s = timeout_s
        self.max_attempts = max(1, int(max_attempts))
        # url -> unix_ts when it can be retried
        self._cooldown_until: dict[str, float] = {}
        self._idx = 0

    def _pick_url(self) -> str:
        if not self.urls:
            raise RpcError("misconfig", "No RPC URLs configured (set RPC_URL or RPC_URLS).")
        n = len(self.urls)
        for _ in range(n):
            u = self.urls[self._idx % n]
            self._idx += 1
            until = self._cooldown_until.get(u, 0.0)
            if time.time() >= until:
                return u
        # all cooled down, pick one (oldest cooldown) to avoid deadlock
        return min(self.urls, key=lambda u: self._cooldown_until.get(u, 0.0))

    def _cooldown(self, url: str, seconds: float) -> None:
        seconds = float(seconds)
        jitter = random.uniform(0.0, min(2.0, seconds * 0.1))
        self._cooldown_until[url] = max(self._cooldown_until.get(url, 0.0), time.time() + seconds + jitter)

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params

        last_err: Exception | None = None
        for attempt in range(self.max_attempts):
            url = self._pick_url()
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout_s)
                if resp.status_code == 429:
                    self._cooldown(url, seconds=min(300.0, 10.0 * (attempt + 1)))
                    last_err = RpcError("rate_limited", f"{url}")
                    continue
                resp.raise_for_status()
                data = resp.json() or {}
                if "error" in data:
                    # treat some provider errors as throttling
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = str(err.get("message") or err)
                    else:
                        msg = str(err)
                    ml = msg.lower()
                    throttled = (
                        ("rate" in ml)
                        or ("too many" in ml)
                        or ("quota" in ml)
                        or ("limit exceeded" in ml)
                        or ("credits" in ml)
                        or ("compute units" in ml)
                        or ("usage limit" in ml)
                    )
                    if throttled:
                        self._cooldown(url, seconds=min(300.0, 10.0 * (attempt + 1)))
                        last_err = RpcError("rate_limited", msg)
                        continue
                    raise RpcError("rpc_error", msg)
                return data.get("result")
            except RpcError as e:
                last_err = e
                continue
            except requests.RequestException as e:
                # network / TLS / timeout: rotate quickly
                self._cooldown(url, seconds=min(60.0, 2.0 * (attempt + 1)))
                last_err = e
                continue
            except Exception as e:
                last_err = e
                continue

        if last_err is None:
            raise RpcError("unknown", "rpc call failed")
        if isinstance(last_err, RpcError):
            raise last_err
        raise RpcError("network", str(last_err))
