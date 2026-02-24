#!/usr/bin/env python3
import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_ts(ts):
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def auto_run_id() -> str:
    log_path = Path("logs/meme_bot_early_edge_auto.log")
    if not log_path.exists():
        return ""
    try:
        data = log_path.read_bytes()
        if len(data) > 250_000:
            data = data[-250_000:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if "run_id=" in ln]
        if not lines:
            return ""
        rid = lines[-1].split("run_id=", 1)[1].strip()
        rid = rid.replace("[/dim]", "").split()[0].strip()
        return rid
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Summarize trade outcomes by signal tier/score.")
    ap.add_argument("--db", default="data/positions.db", help="Path to positions db")
    ap.add_argument("--since-hours", type=float, default=24.0, help="Lookback window in hours")
    ap.add_argument("--out", default="data/meme_signal_outcomes.jsonl", help="Output JSONL path")
    ap.add_argument("--min-trades", type=int, default=3, help="Minimum trades to include a bucket")
    ap.add_argument("--run-id", default="", help="optional: filter to a specific run_id")
    ap.add_argument("--auto-run-id", action="store_true", help="auto-detect run_id from bot log")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"missing db: {db_path}")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = auto_run_id()

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT trade_id, mint, side, entry_timestamp, exit_timestamp, pnl_usd, pnl_pct, metadata FROM trades")

    buckets = defaultdict(list)
    winner_buckets = defaultdict(list)
    liq_mode_stats = defaultdict(list)
    scout_mode_stats = defaultdict(list)
    liq_winner_stats = defaultdict(list)
    tier_stats = defaultdict(list)
    total = 0
    for trade_id, mint, side, entry_ts, exit_ts, pnl_usd, pnl_pct, metadata in cur.fetchall():
        if str(side or "").upper() != "SELL":
            continue
        # In this bot we record realized PnL legs as SELL rows (partial exits included).
        # Older versions of this report filtered to BUY and produced empty output.
        dt = parse_ts(exit_ts) or parse_ts(entry_ts)
        if dt and dt < cutoff:
            continue
        meta = {}
        try:
            meta = json.loads(metadata or "{}")
        except json.JSONDecodeError:
            meta = {}
        if run_id:
            mrid = str(meta.get("run_id") or "").strip()
            if mrid != run_id:
                continue
        total += 1
        score = meta.get("signal_score")
        tier = meta.get("signal_tier")
        if score is None and tier is None:
            continue
        pnl = float(pnl_usd or 0.0)
        pct = float(pnl_pct or 0.0)
        if tier:
            tier_stats[tier].append((pnl, pct))
        if score is not None:
            # bucket scores into whole-number bins
            try:
                s = float(score)
                key = f"{int(s)}-{int(s)+1}"
                buckets[key].append((pnl, pct))
            except Exception:
                pass
        wscore = meta.get("winner_score")
        if wscore is not None:
            try:
                ws = max(0.0, float(wscore))
                lo = int(ws // 5) * 5
                hi = lo + 5
                wkey = f"{lo}-{hi}"
                winner_buckets[wkey].append((pnl, pct))
            except Exception:
                pass
        liq_est = meta.get("liquidity_estimated")
        if liq_est is not None:
            mode = "estimated" if bool(liq_est) else "observed"
            liq_mode_stats[mode].append((pnl, pct))
            if wscore is not None:
                try:
                    ws = max(0.0, float(wscore))
                    lo = int(ws // 5) * 5
                    hi = lo + 5
                    lw_key = f"{mode}:{lo}-{hi}"
                    liq_winner_stats[lw_key].append((pnl, pct))
                except Exception:
                    pass
        scout_mode = meta.get("mcap_scout_mode")
        if scout_mode is not None:
            smode = "scout" if bool(scout_mode) else "strict"
            scout_mode_stats[smode].append((pnl, pct))

    def summarize(items):
        n = len(items)
        wins = sum(1 for pnl, _ in items if pnl > 0)
        avg_pnl = sum(pnl for pnl, _ in items) / n if n else 0.0
        avg_pct = sum(pct for _, pct in items) / n if n else 0.0
        return {"trades": n, "win_rate": round(wins / n, 3) if n else 0.0, "avg_pnl_usd": round(avg_pnl, 4), "avg_pnl_pct": round(avg_pct, 4)}

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "since_hours": args.since_hours,
        "run_id": run_id or None,
        "total_trades": total,
        "by_tier": {k: summarize(v) for k, v in tier_stats.items() if len(v) >= args.min_trades},
        "by_score_bucket": {k: summarize(v) for k, v in buckets.items() if len(v) >= args.min_trades},
        "by_winner_score_bucket": {k: summarize(v) for k, v in winner_buckets.items() if len(v) >= args.min_trades},
        "by_liquidity_estimated": {k: summarize(v) for k, v in liq_mode_stats.items() if len(v) >= args.min_trades},
        "by_scout_mode": {k: summarize(v) for k, v in scout_mode_stats.items() if len(v) >= args.min_trades},
        "by_liquidity_winner_bucket": {k: summarize(v) for k, v in liq_winner_stats.items() if len(v) >= args.min_trades},
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report) + "\n")

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
