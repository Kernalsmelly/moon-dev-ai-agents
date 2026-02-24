#!/usr/bin/env python3
"""Generate a lightweight HTML + JSON dashboard for meme edge stats."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB_PATH = BASE / "data" / "positions.db"
SIGNALS = Path(os.getenv("MEME_LAUNCH_SIGNALS_FILE", str(BASE / "data" / "meme_launch_signals.jsonl")))
OUT_JSON = BASE / "data" / "edge_dashboard.json"
OUT_HTML = BASE / "data" / "edge_dashboard.html"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


def _count_lines(path: Path, max_lines: int | None = None) -> int:
    if not path.exists():
        return 0
    if max_lines is None:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    # fast tail count
    n = 0
    with open(path, "rb") as fh:
        for line in fh:
            n += 1
            if max_lines and n >= max_lines:
                break
    return n


def _query_window(cur: sqlite3.Cursor, minutes: int) -> dict[str, Any]:
    # exit_timestamp is stored as ISO with T, so normalize to sqlite datetime.
    rows = cur.execute(
        """
        SELECT pnl_usd, exit_reason
        FROM trades
        WHERE side='SELL'
          AND replace(exit_timestamp,'T',' ') >= datetime('now','localtime', ?)
        """,
        (f"-{minutes} minutes",),
    ).fetchall()

    out = {
        "n": 0,
        "pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "avg_win": None,
        "avg_loss": None,
        "reasons": {},
    }
    if not rows:
        return out

    wins = []
    losses = []
    reasons: dict[str, dict[str, float]] = {}
    pnl_sum = 0.0
    for pnl, reason in rows:
        try:
            p = float(pnl or 0.0)
        except Exception:
            p = 0.0
        pnl_sum += p
        if p > 0:
            wins.append(p)
        elif p < 0:
            losses.append(p)
        r = str(reason or "UNKNOWN")
        if r not in reasons:
            reasons[r] = {"n": 0, "pnl": 0.0}
        reasons[r]["n"] += 1
        reasons[r]["pnl"] += p

    out["n"] = len(rows)
    out["pnl"] = pnl_sum
    out["wins"] = len(wins)
    out["losses"] = len(losses)
    out["avg_win"] = (sum(wins) / len(wins)) if wins else None
    out["avg_loss"] = (sum(losses) / len(losses)) if losses else None
    out["reasons"] = reasons
    return out


def _top_reasons(reasons: dict[str, dict[str, float]], limit: int = 6) -> list[dict[str, Any]]:
    items = [{"reason": k, "n": int(v.get("n", 0)), "pnl": float(v.get("pnl", 0.0))} for k, v in reasons.items()]
    items.sort(key=lambda x: x["pnl"])
    return items[:limit]


def _recent_trades(cur: sqlite3.Cursor, limit: int = 20) -> list[dict[str, Any]]:
    rows = cur.execute(
        """
        SELECT exit_timestamp, symbol, exit_reason, pnl_usd
        FROM trades
        WHERE side='SELL'
        ORDER BY exit_timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for ts, sym, reason, pnl in rows:
        out.append({
            "ts": ts,
            "symbol": sym,
            "reason": reason,
            "pnl": float(pnl or 0.0),
        })
    return out


def _render_html(payload: dict[str, Any]) -> str:
    def fmt_money(v: float | None) -> str:
        if v is None:
            return "-"
        return f"${v:+.2f}"

    def fmt_pct(n: int, d: int) -> str:
        if d <= 0:
            return "0%"
        return f"{(100.0*n/d):.0f}%"

    windows = payload.get("windows", {})
    rows = []
    for label, w in windows.items():
        rows.append(
            f"<tr><td>{label}</td><td>{w['n']}</td><td>{fmt_money(w['pnl'])}</td>"
            f"<td>{fmt_pct(w['wins'], w['n'])}</td><td>{fmt_money(w['avg_win'])}</td><td>{fmt_money(w['avg_loss'])}</td></tr>"
        )

    reason_rows = []
    for label, w in windows.items():
        tops = _top_reasons(w.get("reasons", {}), limit=6)
        if not tops:
            continue
        reason_rows.append(f"<h3>{label} top drags</h3>")
        reason_rows.append("<table><tr><th>Reason</th><th>Count</th><th>PnL</th></tr>")
        for r in tops:
            reason_rows.append(
                f"<tr><td>{r['reason']}</td><td>{r['n']}</td><td>{fmt_money(r['pnl'])}</td></tr>"
            )
        reason_rows.append("</table>")

    recent_rows = []
    for t in payload.get("recent_trades", []):
        recent_rows.append(
            f"<tr><td>{t['ts']}</td><td>{t['symbol']}</td><td>{t['reason']}</td><td>{fmt_money(t['pnl'])}</td></tr>"
        )

    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Meme Edge Dashboard</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system; margin: 24px; color: #111; }}
    h1 {{ margin: 0 0 8px 0; }}
    .meta {{ color: #555; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; margin: 12px 0 20px; width: 100%; max-width: 900px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
    th {{ background: #f5f5f5; }}
  </style>
</head>
<body>
  <h1>Meme Edge Dashboard</h1>
  <div class="meta">Updated: {payload.get('generated_at')} | Signals: {payload.get('signals_count')}</div>
  <h3>Window Summary</h3>
  <table>
    <tr><th>Window</th><th>Trades</th><th>P&L</th><th>Win%</th><th>Avg Win</th><th>Avg Loss</th></tr>
    {''.join(rows)}
  </table>
  {''.join(reason_rows)}
  <h3>Recent Exits</h3>
  <table>
    <tr><th>Time</th><th>Symbol</th><th>Reason</th><th>P&L</th></tr>
    {''.join(recent_rows)}
  </table>
</body>
</html>
"""
    return html


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-json", default=str(OUT_JSON))
    p.add_argument("--out-html", default=str(OUT_HTML))
    args = p.parse_args()

    payload: dict[str, Any] = {
        "generated_at": _now_iso(),
        "signals_count": _count_lines(SIGNALS),
        "windows": {},
        "recent_trades": [],
    }

    if DB_PATH.exists():
        con = sqlite3.connect(str(DB_PATH))
        try:
            cur = con.cursor()
            payload["windows"]["30m"] = _query_window(cur, 30)
            payload["windows"]["2h"] = _query_window(cur, 120)
            payload["windows"]["24h"] = _query_window(cur, 1440)
            payload["recent_trades"] = _recent_trades(cur, 20)
        finally:
            con.close()

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(_render_html(payload), encoding="utf-8")

    print(f"meme_edge_dashboard: wrote {out_json} and {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
