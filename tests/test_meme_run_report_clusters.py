from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "meme_run_report.py"
SPEC = importlib.util.spec_from_file_location("meme_run_report", MODULE_PATH)
assert SPEC and SPEC.loader
meme_run_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = meme_run_report
SPEC.loader.exec_module(meme_run_report)


def _trade(*, trade_id: int, mint: str, symbol: str, exit_ts: str, pnl: float, hold_s: float | None):
    md = {"run_id": "run_test"}
    if hold_s is not None:
        md["hold_time_sec"] = hold_s
    return meme_run_report.TradeRow(
        trade_id=trade_id,
        mint=mint,
        symbol=symbol,
        exit_reason="TP0",
        pnl_usd=pnl,
        pnl_pct=1.0,
        amount_usd=10.0,
        exit_timestamp=exit_ts,
        metadata=md,
    )


def test_cluster_uses_reconstructed_entry_anchor():
    # Trades 1/2 share same implied entry: 10:00:00.
    rows = [
        _trade(
            trade_id=1,
            mint="MINTA",
            symbol="A",
            exit_ts="2026-02-12T10:01:00",
            pnl=1.0,
            hold_s=60.0,
        ),
        _trade(
            trade_id=2,
            mint="MINTA",
            symbol="A",
            exit_ts="2026-02-12T10:03:00",
            pnl=0.5,
            hold_s=180.0,
        ),
        # New position on same mint, implied entry 10:11:00.
        _trade(
            trade_id=3,
            mint="MINTA",
            symbol="A",
            exit_ts="2026-02-12T10:12:00",
            pnl=-0.2,
            hold_s=60.0,
        ),
    ]

    clusters = meme_run_report._cluster_trades(rows, entry_tolerance_sec=90, gap_fallback_sec=600)
    assert len(clusters) == 2
    totals = sorted((c.trade_count, round(c.pnl_usd, 2)) for c in clusters)
    assert totals == [(1, -0.2), (2, 1.5)]


def test_cluster_fallback_for_missing_hold_time():
    rows = [
        _trade(
            trade_id=10,
            mint="MINTB",
            symbol="B",
            exit_ts="2026-02-12T09:00:00",
            pnl=0.2,
            hold_s=None,
        ),
        _trade(
            trade_id=11,
            mint="MINTB",
            symbol="B",
            exit_ts="2026-02-12T09:05:00",
            pnl=0.4,
            hold_s=None,
        ),
    ]
    clusters = meme_run_report._cluster_trades(rows, gap_fallback_sec=360)
    assert len(clusters) == 1
    assert clusters[0].trade_count == 2
    assert round(clusters[0].pnl_usd, 2) == 0.6
