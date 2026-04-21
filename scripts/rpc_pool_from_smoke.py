#!/usr/bin/env python3
"""Generate data/rpc_pool.json from provider_smoke_test.json.

- Keeps only RPC endpoints marked ok
- Sorts by RTT (ascending) when available
- Avoids printing full secrets in stdout
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SENSITIVE_QUERY_KEYS = {"api-key", "apikey", "key", "token", "access_token", "auth"}


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        q = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            q.append((k, "***" if k.lower() in SENSITIVE_QUERY_KEYS else v))
        query = urlencode(q)
        segs = parts.path.split("/")
        new_segs = []
        for s in segs:
            # Redact long path tokens that are likely API keys.
            if len(s) >= 16 and all(ch.isalnum() or ch in "-_." for ch in s):
                new_segs.append("***")
            else:
                new_segs.append(s)
        path = "/".join(new_segs)
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        return url


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", default="data/provider_smoke_test.json")
    p.add_argument("--out", dest="out", default="data/rpc_pool.json")
    args = p.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)
    if not inp.exists():
        raise SystemExit(f"missing {inp}")

    obj = json.loads(inp.read_text(encoding="utf-8"))
    rpc = obj.get("rpc", {}) if isinstance(obj, dict) else {}
    entries = []
    best_by_url: dict[str, dict] = {}
    for name, info in rpc.items():
        if not isinstance(info, dict):
            continue
        if not info.get("ok"):
            continue
        url = info.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        rtt = info.get("rtt_ms")
        try:
            rtt = float(rtt) if rtt is not None else None
        except Exception:
            rtt = None
        # Skip devnet by default; this pool is meant for mainnet operations.
        allow_devnet = os.getenv("RPC_POOL_ALLOW_DEVNET", "").strip().lower() in ("1", "true", "yes")
        if (not allow_devnet) and ("devnet" in url.lower()):
            continue
        candidate = {"url": url, "success_getLatestBlockhash": True, "rtt_ms": rtt, "name": name}
        prev = best_by_url.get(url)
        if prev is None:
            best_by_url[url] = candidate
            continue
        prev_rtt = prev.get("rtt_ms")
        if prev_rtt is None or (rtt is not None and rtt < prev_rtt):
            best_by_url[url] = candidate

    # Sort by RTT if available, otherwise keep original order.
    entries = list(best_by_url.values())
    entries.sort(key=lambda e: (e["rtt_ms"] is None, e["rtt_ms"] or 999999))

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"pool": entries}, fh, indent=2)

    # stdout summary (redacted)
    print(f"rpc_pool_from_smoke: wrote {len(entries)} endpoints -> {out}")
    for e in entries[:8]:
        print(f"  ok {e.get('name')}: {redact_url(str(e.get('url')))} rtt_ms={e.get('rtt_ms')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
