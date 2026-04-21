#!/usr/bin/env python3
"""Compare v1 and v2 paper-trade overlays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
V1_JSON = REPORTS / "meme_decision_paper_overlay_report.json"
V2_JSON = REPORTS / "meme_decision_paper_overlay_v2_report.json"
TRANSITION_JSON = REPORTS / "meme_transition_trade_research.json"
OUT_JSON = REPORTS / "meme_decision_paper_overlay_compare.json"
OUT_MD = REPORTS / "meme_decision_paper_overlay_compare.md"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("summary") or {})


def main() -> None:
    v1 = _load_json(V1_JSON, {})
    v2 = _load_json(V2_JSON, {})
    research = _load_json(TRANSITION_JSON, {})
    rsum = dict(research.get("summary") or {})

    s1 = _summary(v1)
    s2 = _summary(v2)
    compare = {
        "v1": s1,
        "v2": s2,
        "transition_summary": rsum,
    }
    OUT_JSON.write_text(json.dumps(compare, indent=2), encoding="utf-8")

    best30 = dict(rsum.get("best_30m_entry") or {})
    best60 = dict(rsum.get("best_60m_entry") or {})
    best60path = dict(rsum.get("best_60m_path_entry") or {})

    lines = [
        "# Paper Overlay Comparison",
        "",
        "Compare the original late-confirmation paper overlay against the earlier-entry v2 overlay.",
        "",
        "## Why V2 Exists",
        "",
        "The transition research showed that buying only after 30m/60m confirmation is often too late.",
        "We are now testing an earlier starter entry with promotion-as-confirmation instead of promotion-as-first-buy.",
        "",
        "## Current Comparison",
        "",
        "| Overlay | Open | Closed | Winrate | Avg Return | Median Return |",
        "|---|---:|---:|---:|---:|---:|",
        f"| v1 late-confirm | {s1.get('open_positions', 0)} | {s1.get('closed_positions', 0)} | {_fmt_pct(s1.get('closed_winrate'))} | {_fmt_pct(s1.get('closed_avg_return'))} | {_fmt_pct(s1.get('closed_median_return'))} |",
        f"| v2 starter+add | {s2.get('open_positions', 0)} | {s2.get('closed_positions', 0)} | {_fmt_pct(s2.get('closed_winrate'))} | {_fmt_pct(s2.get('closed_avg_return'))} | {_fmt_pct(s2.get('closed_median_return'))} |",
        "",
        "## Transition Research Context",
        "",
        f"- Matured useful winners studied: `{rsum.get('matured_useful_winners', 'n/a')}`",
        f"- Best 30m entry: `{best30.get('bucket', 'n/a')}` avg `{_fmt_pct(best30.get('avg_return'))}`",
        f"- Best 60m entry: `{best60.get('bucket', 'n/a')}` avg `{_fmt_pct(best60.get('avg_return'))}`",
        f"- Best 60m path: `{best60path.get('bucket', 'n/a')}` avg `{_fmt_pct(best60path.get('avg_return'))}`",
        "",
        "## Interpretation",
        "",
        "- v1 tells us how bad it can get if we wait for very late confirmation.",
        "- v2 is the new hypothesis: enter earlier, then use lifecycle and shape to manage the trade.",
        "- We should judge v2 prospectively over the next clean windows, not from a single cycle.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(
        "meme_decision_paper_overlay_compare: "
        f"v1_closed={s1.get('closed_positions', 0)} "
        f"v2_closed={s2.get('closed_positions', 0)}"
    )


if __name__ == "__main__":
    main()
