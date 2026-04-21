#!/usr/bin/env python3
"""Build a wallet allowlist from historical ws_logs signal + outcome data.

This is a bootstrap path for the wallet-outlier lane when external leaderboard
APIs are unavailable or rate-limited. It is intentionally conservative:

- only looks at source=ws_logs rows that contain buyer_wallets
- scores wallets with shrinkage and age penalties
- caps outsized returns so one absurd winner does not dominate the rank
- writes the same JSON shape expected by wallet_outlier_signal_listener.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SIGNALS_FILE = (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or os.path.join(DATA_DIR, "meme_launch_signals.jsonl")
OUTCOMES_FILE = (os.getenv("SIGNAL_OUTCOMES_FILE") or "").strip() or os.path.join(DATA_DIR, "signal_outcomes.jsonl")
ALLOWLIST_FILE = (os.getenv("MEME_LEADERBOARD_ALLOWLIST_FILE") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_allowlist.json"
)

WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v if v is not None else default)
    except Exception:
        return float(default)


def _iter_wallets(metrics: dict[str, Any]) -> list[str]:
    raw = metrics.get("buyer_wallets")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in raw:
        if not isinstance(v, str):
            continue
        w = v.strip()
        if not w or not WALLET_RE.match(w) or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _clip_ret(ret: float) -> float:
    return max(-0.90, min(2.50, float(ret)))


def _score_wallet(
    *,
    signals_n: int,
    signal_avg: float,
    outcomes_n: int,
    win_rate: float,
    avg_ret: float,
    ret_ema: float,
    age_h: float,
) -> float:
    conf_signal = min(1.0, float(signals_n) / 8.0)
    conf_outcome = min(1.0, float(outcomes_n) / 6.0)
    conf = max(conf_signal * 0.35 + conf_outcome * 0.65, 0.15)

    edge = 0.0
    edge += (signal_avg - 35.0) * 0.45
    edge += (win_rate - 0.50) * 32.0
    edge += max(-0.40, min(1.25, avg_ret)) * 18.0
    edge += max(-0.30, min(1.00, ret_ema)) * 14.0

    # Do not trust stale wallets as much. We keep them around for bootstrapping,
    # but the score decays hard after ~2 days.
    age_penalty = min(28.0, max(0.0, age_h - 24.0) * 0.55)

    sample_bonus = min(8.0, float(signals_n + outcomes_n) * 0.9)
    score = 50.0 + (edge * conf) + sample_bonus - age_penalty
    return max(0.0, min(100.0, score))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build bootstrap wallet allowlist from ws_logs history.")
    ap.add_argument("--lookback-hours", type=float, default=float(os.getenv("MEME_WALLET_BOOTSTRAP_LOOKBACK_HOURS", "168") or 168))
    ap.add_argument("--horizon-s", type=int, default=int(os.getenv("MEME_WALLET_OUTLIER_OUTCOME_HORIZON_S", "300") or 300))
    ap.add_argument("--min-signals", type=int, default=int(os.getenv("MEME_WALLET_BOOTSTRAP_MIN_SIGNALS", "2") or 2))
    ap.add_argument("--min-outcomes", type=int, default=int(os.getenv("MEME_WALLET_BOOTSTRAP_MIN_OUTCOMES", "1") or 1))
    ap.add_argument("--min-score", type=float, default=float(os.getenv("MEME_WALLET_BOOTSTRAP_MIN_SCORE", "60") or 60))
    ap.add_argument("--max-wallets", type=int, default=int(os.getenv("MEME_WALLET_BOOTSTRAP_MAX_WALLETS", "120") or 120))
    ap.add_argument("--source", default="ws_logs")
    ap.add_argument("--out", default=ALLOWLIST_FILE)
    args = ap.parse_args()

    now = time.time()
    cutoff = now - (max(1.0, float(args.lookback_hours)) * 3600.0)
    source = str(args.source or "ws_logs").strip()

    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "signals_n": 0,
            "signal_score_sum": 0.0,
            "outcomes_n": 0,
            "wins": 0,
            "ret_sum": 0.0,
            "ret_ema": 0.0,
            "last_seen": 0.0,
        }
    )

    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = _to_float(obj.get("ts"), 0.0)
                if ts <= 0 or ts < cutoff:
                    continue
                metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
                row_source = str(metrics.get("source") or obj.get("source") or "").strip()
                if row_source != source:
                    continue
                wallets = _iter_wallets(metrics)
                if not wallets:
                    continue
                score = _to_float(obj.get("score"), 0.0)
                for wallet in wallets:
                    st = stats[wallet]
                    st["signals_n"] += 1
                    st["signal_score_sum"] += score
                    st["last_seen"] = max(_to_float(st.get("last_seen")), ts)

    if os.path.exists(OUTCOMES_FILE):
        with open(OUTCOMES_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                horizon_s = int(_to_float(obj.get("horizon_s"), -1))
                if horizon_s != int(args.horizon_s):
                    continue
                ts = _to_float(obj.get("signal_ts") or obj.get("ts"), 0.0)
                if ts <= 0 or ts < cutoff:
                    continue
                metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
                row_source = str(metrics.get("source") or obj.get("source") or "").strip()
                if row_source != source:
                    continue
                wallets = _iter_wallets(metrics)
                if not wallets:
                    continue
                ret = _clip_ret(_to_float(obj.get("ret"), 0.0))
                for wallet in wallets:
                    st = stats[wallet]
                    st["outcomes_n"] += 1
                    st["ret_sum"] += ret
                    if ret >= 0.12:
                        st["wins"] += 1
                    prev_ema = _to_float(st.get("ret_ema"), 0.0)
                    alpha = 0.25
                    st["ret_ema"] = prev_ema + alpha * (ret - prev_ema)
                    st["last_seen"] = max(_to_float(st.get("last_seen")), ts)

    rows: list[dict[str, Any]] = []
    for wallet, st in stats.items():
        signals_n = int(st.get("signals_n") or 0)
        outcomes_n = int(st.get("outcomes_n") or 0)
        if signals_n < int(args.min_signals):
            continue
        if outcomes_n < int(args.min_outcomes):
            continue
        signal_avg = (_to_float(st.get("signal_score_sum")) / float(signals_n)) if signals_n > 0 else 0.0
        win_rate = (float(int(st.get("wins") or 0)) / float(outcomes_n)) if outcomes_n > 0 else 0.0
        avg_ret = (_to_float(st.get("ret_sum")) / float(outcomes_n)) if outcomes_n > 0 else 0.0
        ret_ema = _to_float(st.get("ret_ema"), 0.0)
        last_seen = _to_float(st.get("last_seen"), 0.0)
        age_h = ((now - last_seen) / 3600.0) if last_seen > 0 else 1e9
        score = _score_wallet(
            signals_n=signals_n,
            signal_avg=signal_avg,
            outcomes_n=outcomes_n,
            win_rate=win_rate,
            avg_ret=avg_ret,
            ret_ema=ret_ema,
            age_h=age_h,
        )
        if score < float(args.min_score):
            continue
        rows.append(
            {
                "wallet": wallet,
                "score": round(score, 2),
                "n": outcomes_n,
                "signals_n": signals_n,
                "outcomes_n": outcomes_n,
                "win_rate_ema": round(win_rate, 4),
                "pnl_ema": round(ret_ema, 4),
                "avg_ret": round(avg_ret, 4),
                "signal_avg": round(signal_avg, 2),
                "last_seen": last_seen,
                "source": "bootstrap_ws_logs",
            }
        )

    rows.sort(key=lambda row: (float(row.get("score") or 0.0), float(row.get("last_seen") or 0.0)), reverse=True)
    rows = rows[: max(1, int(args.max_wallets))]
    by_wallet = {str(row["wallet"]): row for row in rows}
    out = {
        "ts": now,
        "count": len(rows),
        "params": {
            "source": source,
            "lookback_hours": float(args.lookback_hours),
            "horizon_s": int(args.horizon_s),
            "min_signals": int(args.min_signals),
            "min_outcomes": int(args.min_outcomes),
            "min_score": float(args.min_score),
            "max_wallets": int(args.max_wallets),
        },
        "wallets": rows,
        "by_wallet": by_wallet,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {args.out} wallets={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
