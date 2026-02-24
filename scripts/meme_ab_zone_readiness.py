#!/usr/bin/env python3
"""Compute readiness to promote winner-zone policy from A/B summary."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
load_dotenv(dotenv_path=str(BASE / ".env"), override=True)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        if x is None:
            return d
        return float(x)
    except Exception:
        return d


def _i(x: Any, d: int = 0) -> int:
    try:
        if x is None:
            return d
        return int(x)
    except Exception:
        return d


def _evaluate(summary: dict[str, Any]) -> dict[str, Any]:
    b = summary.get("base") if isinstance(summary.get("base"), dict) else {}
    z = summary.get("zone") if isinstance(summary.get("zone"), dict) else {}

    b_tr = _i(b.get("trades"))
    z_tr = _i(z.get("trades"))
    b_wr = _f(b.get("winrate"))
    z_wr = _f(z.get("winrate"))
    b_pnl = _f(b.get("pnl_usd"))
    z_pnl = _f(z.get("pnl_usd"))
    b_tail = _f(b.get("loss_cluster_share"))
    z_tail = _f(z.get("loss_cluster_share"))
    b_dom = _f(b.get("dominant_cluster_leg_share"))
    z_dom = _f(z.get("dominant_cluster_leg_share"))

    min_trades_each = int(os.getenv("MEME_AB_ZONE_READY_MIN_TRADES_EACH", "20") or 20)
    min_zone_trade_ratio = float(os.getenv("MEME_AB_ZONE_READY_MIN_ZONE_TRADE_RATIO", "0.35") or 0.35)
    wr_slack_pp = float(os.getenv("MEME_AB_ZONE_READY_WR_SLACK_PP", "2.0") or 2.0) / 100.0
    min_pnl_delta = float(os.getenv("MEME_AB_ZONE_READY_MIN_PNL_DELTA_USD", "0.0") or 0.0)
    tail_slack_pp = float(os.getenv("MEME_AB_ZONE_READY_TAIL_SLACK_PP", "0.0") or 0.0) / 100.0
    dom_slack_pp = float(os.getenv("MEME_AB_ZONE_READY_DOM_SLACK_PP", "0.0") or 0.0) / 100.0
    max_zone_tail_abs = float(os.getenv("MEME_AB_ZONE_READY_MAX_ZONE_TAIL_ABS", "0.75") or 0.75)
    max_zone_dom_abs = float(os.getenv("MEME_AB_ZONE_READY_MAX_ZONE_DOM_ABS", "0.80") or 0.80)

    reasons: list[str] = []
    min_zone_trades = max(min_trades_each, int(round(float(max(1, b_tr)) * max(0.0, min_zone_trade_ratio))))
    if b_tr < min_trades_each:
        reasons.append(f"base_trades<{min_trades_each}")
    if z_tr < min_zone_trades:
        reasons.append(f"zone_trades<{min_zone_trades}")
    if z_pnl < (b_pnl + min_pnl_delta):
        reasons.append("zone_pnl_not_better")
    if z_wr < (b_wr - wr_slack_pp):
        reasons.append("zone_wr_too_low")

    tail_cap = min(max_zone_tail_abs, b_tail + tail_slack_pp)
    dom_cap = min(max_zone_dom_abs, b_dom + dom_slack_pp)
    if z_tail > tail_cap:
        reasons.append("zone_tail_too_high")
    if z_dom > dom_cap:
        reasons.append("zone_dom_too_high")

    ready = len(reasons) == 0
    return {
        "generated_at": time.time(),
        "ready": bool(ready),
        "reasons_not_ready": reasons,
        "thresholds": {
            "min_trades_each": min_trades_each,
            "min_zone_trade_ratio": min_zone_trade_ratio,
            "min_zone_trades": min_zone_trades,
            "wr_slack_pp": wr_slack_pp * 100.0,
            "min_pnl_delta_usd": min_pnl_delta,
            "tail_cap": tail_cap,
            "dom_cap": dom_cap,
        },
        "metrics": {
            "base_trades": b_tr,
            "zone_trades": z_tr,
            "base_wr": b_wr,
            "zone_wr": z_wr,
            "base_pnl": b_pnl,
            "zone_pnl": z_pnl,
            "base_tail": b_tail,
            "zone_tail": z_tail,
            "base_dom": b_dom,
            "zone_dom": z_dom,
        },
    }


def _md(obj: dict[str, Any]) -> str:
    lines = [
        "# A/B Zone Readiness",
        "",
        f"- ready: `{obj.get('ready')}`",
        f"- generated_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(float(obj.get('generated_at') or time.time())))}",
        "",
        "## Thresholds",
        "",
    ]
    th = obj.get("thresholds") if isinstance(obj.get("thresholds"), dict) else {}
    for k, v in th.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    m = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    reasons = obj.get("reasons_not_ready") if isinstance(obj.get("reasons_not_ready"), list) else []
    lines.append("## Reasons")
    lines.append("")
    if reasons:
        for r in reasons:
            lines.append(f"- {r}")
    else:
        lines.append("- ready_for_promotion")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(BASE / "data" / "meme_reports" / "ab_zone_latest.json"))
    ap.add_argument("--out-json", default=str(BASE / "data" / "meme_reports" / "ab_zone_ready.json"))
    ap.add_argument("--out-md", default=str(BASE / "data" / "meme_reports" / "ab_zone_ready.md"))
    args = ap.parse_args()

    s = Path(args.summary)
    if not s.exists():
        raise SystemExit(f"missing summary: {s}")
    summary = json.loads(s.read_text(encoding="utf-8"))
    out = _evaluate(summary)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    out_md.write_text(_md(out) + "\n", encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"ready={out.get('ready')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
