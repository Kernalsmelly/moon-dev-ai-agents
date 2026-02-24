#!/usr/bin/env python3
"""Decide next action for baseline vs winner-zone A/B lanes."""

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


def _recommend(summary: dict[str, Any]) -> dict[str, Any]:
    b = summary.get("base") if isinstance(summary.get("base"), dict) else {}
    z = summary.get("zone") if isinstance(summary.get("zone"), dict) else {}
    db = b.get("debug") if isinstance(b.get("debug"), dict) else {}
    dz = z.get("debug") if isinstance(z.get("debug"), dict) else {}

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
    b_dbg = _i(db.get("events"))
    z_dbg = _i(dz.get("events"))
    b_prequote = _f(db.get("prequote_pass_rate"))
    z_prequote = _f(dz.get("prequote_pass_rate"))
    z_match = _i(dz.get("zone_match_passes"))
    z_bypass = _i(dz.get("zone_bypass_passes"))

    min_trades = int(os.getenv("MEME_AB_ZONE_DECIDER_MIN_TRADES", "20") or 20)
    min_debug_events = int(os.getenv("MEME_AB_ZONE_DECIDER_MIN_DEBUG_EVENTS", "40") or 40)
    promote_wr_slack_pp = float(os.getenv("MEME_AB_ZONE_DECIDER_PROMOTE_WR_SLACK_PP", "2.0") or 2.0) / 100.0
    starve_ratio = float(os.getenv("MEME_AB_ZONE_DECIDER_STARVE_RATIO", "0.35") or 0.35)
    min_zone_prequote = float(os.getenv("MEME_AB_ZONE_DECIDER_MIN_ZONE_PREQUOTE_RATE", "0.08") or 0.08)
    min_zone_bypass_passes = int(os.getenv("MEME_AB_ZONE_DECIDER_MIN_BYPASS_PASSES", "5") or 5)
    min_zone_match_passes = int(os.getenv("MEME_AB_ZONE_DECIDER_MIN_ZONE_MATCH_PASSES", "3") or 3)

    # Promotion criteria once we have enough realized-trade sample.
    if b_tr >= min_trades and z_tr >= min_trades:
        better_or_equal_wr = z_wr >= (b_wr - promote_wr_slack_pp)
        better_pnl = z_pnl >= b_pnl
        better_tail = z_tail <= b_tail
        better_dom = z_dom <= b_dom
        if better_or_equal_wr and better_pnl and better_tail and better_dom:
            return {
                "action": "promote_zone",
                "why": "Zone lane outperformed or matched base on PnL and concentration risk at sufficient sample.",
                "change": {"MEME_WINNER_ZONE_ENABLED": "1"},
                "metrics": {
                    "base_trades": b_tr,
                    "zone_trades": z_tr,
                    "base_pnl": b_pnl,
                    "zone_pnl": z_pnl,
                    "base_wr": b_wr,
                    "zone_wr": z_wr,
                    "base_tail": b_tail,
                    "zone_tail": z_tail,
                    "base_dom": b_dom,
                    "zone_dom": z_dom,
                },
            }
        return {
            "action": "hold_ab",
            "why": "Trade sample is sufficient but zone lane does not yet dominate base on key metrics.",
            "change": {},
            "metrics": {
                "base_trades": b_tr,
                "zone_trades": z_tr,
                "base_pnl": b_pnl,
                "zone_pnl": z_pnl,
                "base_wr": b_wr,
                "zone_wr": z_wr,
                "base_tail": b_tail,
                "zone_tail": z_tail,
                "base_dom": b_dom,
                "zone_dom": z_dom,
            },
        }

    # Before trade sample is ready, tune bypass if zone lane is starved at prequote.
    debug_ready = (b_dbg + z_dbg) >= max(1, min_debug_events)
    zone_starved = (z_prequote < min_zone_prequote) or (b_prequote > 0 and z_prequote < (b_prequote * starve_ratio))
    if debug_ready and zone_starved and z_bypass < min_zone_bypass_passes:
        cur_score = _f(os.getenv("MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE", "70"), 70.0)
        cur_hits = _i(os.getenv("MEME_AB_ZONE_BYPASS_MIN_HITS", "5"), 5)
        cur_net = _f(os.getenv("MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN", "2.0"), 2.0)
        nxt_score = max(64.0, cur_score - 2.0)
        nxt_hits = max(4, cur_hits - 1)
        nxt_net = max(1.6, round(cur_net - 0.2, 2))
        return {
            "action": "loosen_zone_bypass",
            "why": "Zone lane is starved versus base in prequote flow with no bypass passes.",
            "change": {
                "MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE": f"{nxt_score:.0f}",
                "MEME_AB_ZONE_BYPASS_MIN_HITS": str(nxt_hits),
                "MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN": f"{nxt_net:.2f}",
            },
            "metrics": {
                "base_debug_events": b_dbg,
                "zone_debug_events": z_dbg,
                "base_prequote_rate": b_prequote,
                "zone_prequote_rate": z_prequote,
                "zone_bypass_passes": z_bypass,
                "min_zone_bypass_passes": min_zone_bypass_passes,
            },
        }

    # If bypass is working but true zone matches are rare, widen builder constraints
    # so generated zones cover more valid flow (without immediately disabling zone discipline).
    if debug_ready and z_bypass >= min_zone_bypass_passes and z_match < min_zone_match_passes:
        cur_s = _i(os.getenv("MEME_WINNER_ZONE_COARSE_MIN_SAMPLES", "5"), 5)
        cur_wr = _f(os.getenv("MEME_WINNER_ZONE_COARSE_MIN_WIN_RATE", "0.45"), 0.45)
        cur_mean = _f(os.getenv("MEME_WINNER_ZONE_COARSE_MIN_MEAN_ADJ", "-0.005"), -0.005)
        nxt_s = max(3, cur_s - 1)
        nxt_wr = max(0.40, round(cur_wr - 0.02, 2))
        nxt_mean = max(-0.01, round(cur_mean - 0.002, 3))
        return {
            "action": "widen_zone_builder",
            "why": "Zone bypass is carrying flow, but direct zone matches are still too sparse.",
            "change": {
                "MEME_WINNER_ZONE_COARSE_MIN_SAMPLES": str(nxt_s),
                "MEME_WINNER_ZONE_COARSE_MIN_WIN_RATE": f"{nxt_wr:.2f}",
                "MEME_WINNER_ZONE_COARSE_MIN_MEAN_ADJ": f"{nxt_mean:.3f}",
            },
            "metrics": {
                "zone_match_passes": z_match,
                "zone_bypass_passes": z_bypass,
                "min_zone_match_passes": min_zone_match_passes,
                "base_prequote_rate": b_prequote,
                "zone_prequote_rate": z_prequote,
            },
        }

    return {
        "action": "hold_collect",
        "why": "Collect more sample before changing zone policy.",
        "change": {},
        "metrics": {
            "base_trades": b_tr,
            "zone_trades": z_tr,
            "base_debug_events": b_dbg,
            "zone_debug_events": z_dbg,
            "base_prequote_rate": b_prequote,
            "zone_prequote_rate": z_prequote,
            "zone_match_passes": z_match,
            "zone_bypass_passes": z_bypass,
        },
    }


def _md(rec: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# A/B Zone Decision",
        "",
        f"- generated_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"- action: `{rec.get('action')}`",
        f"- why: {rec.get('why')}",
        "",
        "## Proposed Change",
        "",
    ]
    ch = rec.get("change") if isinstance(rec.get("change"), dict) else {}
    if ch:
        for k, v in ch.items():
            lines.append(f"- `{k}={v}`")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    metrics = rec.get("metrics") if isinstance(rec.get("metrics"), dict) else {}
    for k, v in metrics.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(BASE / "data" / "meme_reports" / "ab_zone_latest.json"))
    ap.add_argument("--out-json", default=str(BASE / "data" / "meme_reports" / "ab_zone_decision.json"))
    ap.add_argument("--out-md", default=str(BASE / "data" / "meme_reports" / "ab_zone_decision.md"))
    args = ap.parse_args()

    s_path = Path(args.summary)
    if not s_path.exists():
        raise SystemExit(f"missing summary: {s_path}")
    summary = json.loads(s_path.read_text(encoding="utf-8"))
    rec = _recommend(summary)
    rec["generated_at"] = time.time()

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
    out_md.write_text(_md(rec, summary) + "\n", encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"action={rec.get('action')} why={rec.get('why')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
