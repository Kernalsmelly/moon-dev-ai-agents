#!/usr/bin/env python3
"""Discover high-edge wallets from stream data and emit boosted copy-trade signals.

This listener is intentionally lightweight:
- It consumes launch signals (for wallet participation) and signal outcomes (for realized edge).
- It scores wallets with shrinkage so tiny sample sizes do not dominate.
- It emits a second launch signal for a mint when one of its early wallets is a strong outlier.

Output rows are appended to MEME_LAUNCH_SIGNALS_FILE with source=wallet_outlier.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

from src.meme_signal_schema import build_launch_signal_payload, normalize_signal_metrics

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

SIGNALS_FILE = (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or os.path.join(DATA_DIR, "meme_launch_signals.jsonl")
OUTCOMES_FILE = (os.getenv("SIGNAL_OUTCOMES_FILE") or "").strip() or os.path.join(DATA_DIR, "signal_outcomes.jsonl")
STATE_FILE = (os.getenv("MEME_WALLET_OUTLIER_STATE") or "").strip() or os.path.join(DATA_DIR, "wallet_outlier_state.json")
LEADERBOARD_FILE = (os.getenv("MEME_WALLET_OUTLIER_LEADERBOARD") or "").strip() or os.path.join(
    DATA_DIR, "wallet_outlier_leaderboard.json"
)
ALLOWLIST_FILE = (os.getenv("MEME_LEADERBOARD_ALLOWLIST_FILE") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_allowlist.json"
)

POLL_S = float(os.getenv("MEME_WALLET_OUTLIER_POLL_S", "4") or 4)
EMIT_COOLDOWN_S = float(os.getenv("MEME_WALLET_OUTLIER_EMIT_COOLDOWN_S", "900") or 900)
MIN_ALPHA_SCORE = float(os.getenv("MEME_WALLET_OUTLIER_MIN_SCORE", "64") or 64)
MIN_OUTCOME_SAMPLES = int(os.getenv("MEME_WALLET_OUTLIER_MIN_OUTCOME_SAMPLES", "4") or 4)
MIN_SIGNAL_SAMPLES = int(os.getenv("MEME_WALLET_OUTLIER_MIN_SIGNAL_SAMPLES", "6") or 6)
MIN_BASE_SIGNAL_SCORE = float(os.getenv("MEME_WALLET_OUTLIER_MIN_BASE_SCORE", "20") or 20)
WIN_RET_THRESHOLD = float(os.getenv("MEME_WALLET_OUTLIER_WIN_RET", "0.12") or 0.12)
OUTCOME_HORIZON_S = int(os.getenv("MEME_WALLET_OUTLIER_OUTCOME_HORIZON_S", "300") or 300)
WALLET_STALE_S = float(os.getenv("MEME_WALLET_OUTLIER_WALLET_STALE_S", "21600") or 21600)
MAX_WALLETS = int(os.getenv("MEME_WALLET_OUTLIER_MAX_WALLETS", "12000") or 12000)
MAX_LEADERBOARD = int(os.getenv("MEME_WALLET_OUTLIER_MAX_LEADERBOARD", "120") or 120)
ALLOWLIST_ENABLED = str(os.getenv("MEME_WALLET_OUTLIER_ALLOWLIST_ENABLED", "true") or "true").lower() in ("1", "true", "yes")
ALLOWLIST_MIN_SCORE = float(os.getenv("MEME_WALLET_OUTLIER_ALLOWLIST_MIN_SCORE", "67") or 67)
ALLOWLIST_MAX_AGE_S = float(os.getenv("MEME_WALLET_OUTLIER_ALLOWLIST_MAX_AGE_S", "43200") or 43200)
WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v if v is not None else default)
    except Exception:
        return float(default)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v if v is not None else default)
    except Exception:
        return int(default)


def _load_state() -> dict[str, Any]:
    out: dict[str, Any] = {
        "signal_offset": 0,
        "outcome_offset": 0,
        "last_emit": {},
        "wallet_stats": {},
    }
    if not os.path.exists(STATE_FILE):
        return out
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if isinstance(raw, dict):
            if isinstance(raw.get("signal_offset"), (int, float)):
                out["signal_offset"] = int(raw.get("signal_offset") or 0)
            if isinstance(raw.get("outcome_offset"), (int, float)):
                out["outcome_offset"] = int(raw.get("outcome_offset") or 0)
            if isinstance(raw.get("last_emit"), dict):
                out["last_emit"] = raw.get("last_emit") or {}
            if isinstance(raw.get("wallet_stats"), dict):
                cleaned: dict[str, Any] = {}
                for k, v in (raw.get("wallet_stats") or {}).items():
                    if isinstance(k, str) and WALLET_RE.match(k) and isinstance(v, dict):
                        cleaned[k] = v
                out["wallet_stats"] = cleaned
    except Exception:
        return out
    return out


def _save_state(state: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _load_external_allowlist() -> dict[str, dict[str, Any]]:
    if not ALLOWLIST_ENABLED:
        return {}
    if not ALLOWLIST_FILE or not os.path.exists(ALLOWLIST_FILE):
        return {}
    out: dict[str, dict[str, Any]] = {}
    now = time.time()
    try:
        with open(ALLOWLIST_FILE, "r", encoding="utf-8") as fh:
            obj = json.load(fh) or {}
        by_wallet = obj.get("by_wallet") if isinstance(obj, dict) else {}
        if not isinstance(by_wallet, dict):
            return {}
        for w, md in by_wallet.items():
            if not isinstance(w, str) or not WALLET_RE.match(w):
                continue
            if not isinstance(md, dict):
                continue
            score = _to_float(md.get("score"), 0.0)
            if score < ALLOWLIST_MIN_SCORE:
                continue
            last_seen = _to_float(md.get("last_seen"), 0.0)
            if ALLOWLIST_MAX_AGE_S > 0 and last_seen > 0 and (now - last_seen) > ALLOWLIST_MAX_AGE_S:
                continue
            out[w] = md
    except Exception:
        return {}
    return out


def _wallets_from_metrics(metrics: dict[str, Any]) -> list[str]:
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


def _ensure_wallet(stats: dict[str, Any], wallet: str) -> dict[str, Any]:
    cur = stats.get(wallet)
    if isinstance(cur, dict):
        return cur
    cur = {
        "signals_n": 0,
        "signals_score_sum": 0.0,
        "signals_high_n": 0,
        "outcomes_n": 0,
        "outcomes_wins": 0,
        "outcomes_ret_sum": 0.0,
        "outcomes_ret_ema": 0.0,
        "last_signal_ts": 0.0,
        "last_outcome_ts": 0.0,
    }
    stats[wallet] = cur
    return cur


def _update_signal_stats(stats: dict[str, Any], wallet: str, signal_score: float, ts: float) -> None:
    st = _ensure_wallet(stats, wallet)
    st["signals_n"] = _to_int(st.get("signals_n")) + 1
    st["signals_score_sum"] = _to_float(st.get("signals_score_sum")) + float(signal_score)
    if float(signal_score) >= 60.0:
        st["signals_high_n"] = _to_int(st.get("signals_high_n")) + 1
    st["last_signal_ts"] = max(_to_float(st.get("last_signal_ts")), float(ts))


def _update_outcome_stats(stats: dict[str, Any], wallet: str, ret: float, ts: float) -> None:
    st = _ensure_wallet(stats, wallet)
    st["outcomes_n"] = _to_int(st.get("outcomes_n")) + 1
    st["outcomes_ret_sum"] = _to_float(st.get("outcomes_ret_sum")) + float(ret)
    if float(ret) >= WIN_RET_THRESHOLD:
        st["outcomes_wins"] = _to_int(st.get("outcomes_wins")) + 1
    prev_ema = _to_float(st.get("outcomes_ret_ema"))
    alpha = 0.18
    st["outcomes_ret_ema"] = prev_ema + alpha * (float(ret) - prev_ema)
    st["last_outcome_ts"] = max(_to_float(st.get("last_outcome_ts")), float(ts))


def _wallet_alpha(st: dict[str, Any], now: float) -> dict[str, float]:
    sn = _to_int(st.get("signals_n"))
    ss = _to_float(st.get("signals_score_sum"))
    sh = _to_int(st.get("signals_high_n"))
    on = _to_int(st.get("outcomes_n"))
    ow = _to_int(st.get("outcomes_wins"))
    ors = _to_float(st.get("outcomes_ret_sum"))
    oema = _to_float(st.get("outcomes_ret_ema"))
    last_signal_ts = _to_float(st.get("last_signal_ts"))
    last_outcome_ts = _to_float(st.get("last_outcome_ts"))
    freshest = max(last_signal_ts, last_outcome_ts)

    signal_avg = (ss / float(sn)) if sn > 0 else 0.0
    signal_high_rate = (float(sh) / float(sn)) if sn > 0 else 0.0
    win_rate = (float(ow) / float(on)) if on > 0 else 0.0
    avg_ret = (ors / float(on)) if on > 0 else 0.0

    # Shrink toward neutral so tiny-sample wallets do not dominate.
    conf_signal = min(1.0, float(sn) / 20.0)
    conf_outcome = min(1.0, float(on) / 10.0)

    signal_edge = ((signal_avg - 42.0) * 0.85) + ((signal_high_rate - 0.33) * 25.0)
    outcome_edge = ((win_rate - 0.50) * 45.0) + (avg_ret * 35.0) + (oema * 20.0)

    raw = 50.0 + (signal_edge * conf_signal) + (outcome_edge * conf_outcome)

    # Age decay: stale wallets should not keep full score.
    age_s = (now - freshest) if freshest > 0 else 1e12
    decay = 1.0
    if age_s > 0 and WALLET_STALE_S > 0:
        decay = max(0.25, min(1.0, 1.0 - (age_s / (WALLET_STALE_S * 4.0))))
    score = max(0.0, min(100.0, raw * decay))

    return {
        "score": float(score),
        "confidence": float(max(conf_signal, conf_outcome)),
        "signal_avg": float(signal_avg),
        "signal_high_rate": float(signal_high_rate),
        "win_rate": float(win_rate),
        "avg_ret": float(avg_ret),
        "signals_n": float(sn),
        "outcomes_n": float(on),
        "freshest": float(freshest),
    }


def _wallet_is_eligible(st: dict[str, Any], alpha: dict[str, float], now: float) -> bool:
    sn = _to_int(st.get("signals_n"))
    on = _to_int(st.get("outcomes_n"))
    if on < MIN_OUTCOME_SAMPLES and sn < MIN_SIGNAL_SAMPLES:
        return False
    if alpha["score"] < MIN_ALPHA_SCORE:
        return False
    if alpha["freshest"] <= 0:
        return False
    if WALLET_STALE_S > 0 and (now - alpha["freshest"]) > WALLET_STALE_S:
        return False
    return True


def _append_payload(payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
    with open(SIGNALS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def _ingest_outcomes(state: dict[str, Any]) -> int:
    if not os.path.exists(OUTCOMES_FILE):
        return 0
    offset = _to_int(state.get("outcome_offset"))
    wallet_stats = state.get("wallet_stats") if isinstance(state.get("wallet_stats"), dict) else {}
    rows = 0
    try:
        with open(OUTCOMES_FILE, "r", encoding="utf-8") as fh:
            if offset > 0:
                fh.seek(offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                offset = fh.tell()
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                horizon_s = _to_int(obj.get("horizon_s"), default=-1)
                if OUTCOME_HORIZON_S > 0 and horizon_s != OUTCOME_HORIZON_S:
                    continue
                ret = obj.get("ret")
                if ret is None:
                    continue
                ret_f = _to_float(ret)
                metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
                wallets = _wallets_from_metrics(metrics)
                if not wallets:
                    continue
                ts = _to_float(obj.get("signal_ts") or obj.get("ts") or time.time())
                for w in wallets:
                    _update_outcome_stats(wallet_stats, w, ret_f, ts)
                rows += 1
    except Exception:
        pass
    state["outcome_offset"] = offset
    state["wallet_stats"] = wallet_stats
    return rows


def _ingest_signals_and_emit(state: dict[str, Any]) -> tuple[int, int]:
    if not os.path.exists(SIGNALS_FILE):
        return 0, 0
    offset = _to_int(state.get("signal_offset"))
    wallet_stats = state.get("wallet_stats") if isinstance(state.get("wallet_stats"), dict) else {}
    last_emit = state.get("last_emit") if isinstance(state.get("last_emit"), dict) else {}
    allowlist = _load_external_allowlist()

    scanned = 0
    emitted = 0
    now = time.time()

    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as fh:
            if offset > 0:
                fh.seek(offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                offset = fh.tell()
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                mint = str(obj.get("mint") or "").strip()
                if not mint:
                    continue
                scanned += 1

                ts = _to_float(obj.get("ts") or time.time())
                first_seen = _to_float(obj.get("first_seen") or ts)
                run_id = str(obj.get("run_id") or "").strip() or None
                base_score = _to_float(obj.get("score"))
                metrics = normalize_signal_metrics(obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {})
                source = str(metrics.get("source") or obj.get("source") or "").strip()
                wallets = _wallets_from_metrics(metrics)

                # Always update signal-side wallet stats from non-wallet_outlier sources.
                if wallets and source != "wallet_outlier":
                    for w in wallets:
                        _update_signal_stats(wallet_stats, w, base_score, ts)

                if source == "wallet_outlier":
                    continue
                if not wallets:
                    continue
                if base_score < MIN_BASE_SIGNAL_SCORE:
                    continue

                prev_emit = _to_float(last_emit.get(mint))
                if EMIT_COOLDOWN_S > 0 and prev_emit > 0 and (now - prev_emit) < EMIT_COOLDOWN_S:
                    continue

                best_wallet = None
                best_alpha = None
                best_allow: dict[str, Any] | None = None
                best_origin = ""
                for w in wallets:
                    st = wallet_stats.get(w)
                    if not isinstance(st, dict):
                        alpha = None
                    else:
                        alpha = _wallet_alpha(st, now)
                    if isinstance(alpha, dict) and _wallet_is_eligible(st, alpha, now):
                        if best_alpha is None or alpha["score"] > best_alpha["score"]:
                            best_alpha = alpha
                            best_wallet = w
                            best_allow = None
                            best_origin = "internal"

                    ext = allowlist.get(w)
                    if isinstance(ext, dict):
                        ext_score = _to_float(ext.get("score"), 0.0)
                        cur_best = -1.0
                        if best_origin == "internal" and isinstance(best_alpha, dict):
                            cur_best = _to_float(best_alpha.get("score"), 0.0)
                        elif best_origin == "leaderboard" and isinstance(best_allow, dict):
                            cur_best = _to_float(best_allow.get("score"), 0.0)
                        if ext_score > cur_best:
                            best_wallet = w
                            best_alpha = None
                            best_allow = ext
                            best_origin = "leaderboard"

                if not best_wallet:
                    continue

                out_metrics = dict(metrics)
                out_metrics["source"] = "wallet_outlier"
                out_metrics["wallet_alpha_wallet"] = best_wallet
                out_metrics["wallet_alpha_origin"] = best_origin or "internal"

                if best_origin == "leaderboard" and isinstance(best_allow, dict):
                    allow_score = _to_float(best_allow.get("score"), 0.0)
                    allow_n = _to_int(best_allow.get("n"), 0)
                    out_metrics["wallet_alpha_score"] = round(allow_score, 2)
                    out_metrics["wallet_alpha_confidence"] = round(max(0.2, min(1.0, float(allow_n) / 8.0)), 3)
                    out_metrics["wallet_alpha_signals_n"] = allow_n
                    out_metrics["wallet_alpha_outcomes_n"] = allow_n
                    if best_allow.get("win_rate_ema") is not None:
                        out_metrics["wallet_alpha_win_rate"] = round(_to_float(best_allow.get("win_rate_ema")), 3)
                    if best_allow.get("pnl_ema") is not None:
                        out_metrics["wallet_alpha_avg_ret"] = round(_to_float(best_allow.get("pnl_ema")), 4)
                    boosted_score = max(base_score, allow_score)
                else:
                    if not isinstance(best_alpha, dict):
                        continue
                    out_metrics["wallet_alpha_score"] = round(best_alpha["score"], 2)
                    out_metrics["wallet_alpha_confidence"] = round(best_alpha["confidence"], 3)
                    out_metrics["wallet_alpha_signal_avg"] = round(best_alpha["signal_avg"], 2)
                    out_metrics["wallet_alpha_signal_high_rate"] = round(best_alpha["signal_high_rate"], 3)
                    out_metrics["wallet_alpha_win_rate"] = round(best_alpha["win_rate"], 3)
                    out_metrics["wallet_alpha_avg_ret"] = round(best_alpha["avg_ret"], 4)
                    out_metrics["wallet_alpha_signals_n"] = int(best_alpha["signals_n"])
                    out_metrics["wallet_alpha_outcomes_n"] = int(best_alpha["outcomes_n"])
                    boosted_score = max(base_score, best_alpha["score"])

                payload = build_launch_signal_payload(
                    mint=mint,
                    metrics=normalize_signal_metrics(out_metrics),
                    score=boosted_score,
                    ts=time.time(),
                    first_seen=first_seen,
                    run_id=run_id,
                )
                _append_payload(payload)
                last_emit[mint] = time.time()
                emitted += 1
    except Exception:
        pass

    state["signal_offset"] = offset
    state["wallet_stats"] = wallet_stats
    state["last_emit"] = last_emit
    return scanned, emitted


def _prune_wallets(state: dict[str, Any], now: float) -> None:
    wallet_stats = state.get("wallet_stats")
    if not isinstance(wallet_stats, dict):
        return
    if len(wallet_stats) <= MAX_WALLETS:
        return
    ranked: list[tuple[str, float, float]] = []
    for w, st in wallet_stats.items():
        if not isinstance(st, dict):
            continue
        alpha = _wallet_alpha(st, now)
        ranked.append((w, alpha["score"], alpha["freshest"]))
    ranked.sort(key=lambda x: (x[1], x[2]), reverse=True)
    keep = {w for (w, _, _) in ranked[:MAX_WALLETS]}
    for w in list(wallet_stats.keys()):
        if w not in keep:
            wallet_stats.pop(w, None)
    state["wallet_stats"] = wallet_stats


def _write_leaderboard(state: dict[str, Any]) -> None:
    wallet_stats = state.get("wallet_stats")
    if not isinstance(wallet_stats, dict):
        return
    now = time.time()
    rows: list[dict[str, Any]] = []
    for w, st in wallet_stats.items():
        if not isinstance(st, dict):
            continue
        alpha = _wallet_alpha(st, now)
        if alpha["score"] < 55:
            continue
        if _to_int(st.get("signals_n")) < 3:
            continue
        rows.append(
            {
                "wallet": w,
                "score": round(alpha["score"], 2),
                "confidence": round(alpha["confidence"], 3),
                "signals_n": _to_int(st.get("signals_n")),
                "outcomes_n": _to_int(st.get("outcomes_n")),
                "signal_avg": round(alpha["signal_avg"], 2),
                "win_rate": round(alpha["win_rate"], 3),
                "avg_ret": round(alpha["avg_ret"], 4),
                "last_signal_ts": _to_float(st.get("last_signal_ts")),
                "last_outcome_ts": _to_float(st.get("last_outcome_ts")),
            }
        )
    rows.sort(key=lambda r: (float(r["score"]), float(r["confidence"])), reverse=True)
    out = {
        "ts": time.time(),
        "count": len(rows),
        "top": rows[: max(1, MAX_LEADERBOARD)],
    }
    try:
        os.makedirs(os.path.dirname(LEADERBOARD_FILE), exist_ok=True)
        with open(LEADERBOARD_FILE, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    except Exception:
        pass


def main() -> int:
    state = _load_state()
    last_log = 0.0
    last_save = 0.0
    last_board = 0.0
    while True:
        now = time.time()
        outcomes_rows = _ingest_outcomes(state)
        scanned, emitted = _ingest_signals_and_emit(state)
        _prune_wallets(state, now)

        if (now - last_board) >= 30:
            last_board = now
            _write_leaderboard(state)

        if outcomes_rows > 0 or emitted > 0 or (now - last_save) >= 20:
            last_save = now
            _save_state(state)

        if (now - last_log) >= 30:
            last_log = now
            wallets_n = len(state.get("wallet_stats") or {})
            print(
                "wallet_outlier "
                f"wallets={wallets_n} outcomes_rows={outcomes_rows} scanned={scanned} emitted={emitted}",
                flush=True,
            )

        time.sleep(max(1.0, POLL_S))


if __name__ == "__main__":
    raise SystemExit(main())
