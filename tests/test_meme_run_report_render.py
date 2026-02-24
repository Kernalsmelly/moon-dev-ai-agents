from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "meme_run_report.py"
SPEC = importlib.util.spec_from_file_location("meme_run_report_render", MODULE_PATH)
assert SPEC and SPEC.loader
meme_run_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = meme_run_report
SPEC.loader.exec_module(meme_run_report)


def _trade(
    *,
    trade_id: int,
    mint: str,
    symbol: str,
    pnl: float,
    exit_ts: str,
    hold_s: float = 60.0,
) -> object:
    return meme_run_report.TradeRow(
        trade_id=trade_id,
        mint=mint,
        symbol=symbol,
        exit_reason="TP0" if pnl >= 0 else "MAX_LOSS_CAP",
        pnl_usd=float(pnl),
        pnl_pct=float(pnl),
        amount_usd=10.0,
        exit_timestamp=exit_ts,
        metadata={"run_id": "run_test", "hold_time_sec": hold_s},
    )


def test_report_losers_section_shows_na_when_no_negative_trades():
    now = datetime.now()
    trades = [
        _trade(trade_id=1, mint="M1", symbol="WINA", pnl=0.5, exit_ts=(now - timedelta(minutes=2)).isoformat(timespec="seconds")),
        _trade(trade_id=2, mint="M1", symbol="WINA", pnl=0.2, exit_ts=(now - timedelta(minutes=1)).isoformat(timespec="seconds")),
    ]
    md = meme_run_report._report("run_test", trades, hours=24)
    assert "Losers:\n- n/a" in md
    assert "Top cluster losers:\n- n/a" in md


def test_report_losers_section_only_includes_negative_symbols():
    now = datetime.now()
    trades = [
        _trade(trade_id=1, mint="M1", symbol="WINA", pnl=0.9, exit_ts=(now - timedelta(minutes=2)).isoformat(timespec="seconds")),
        _trade(trade_id=2, mint="M2", symbol="LOSEB", pnl=-0.4, exit_ts=(now - timedelta(minutes=1)).isoformat(timespec="seconds")),
    ]
    md = meme_run_report._report("run_test", trades, hours=24)
    losers_text = md.split("Losers:\n", 1)[1].split("\n\n", 1)[0]
    assert "`LOSEB`" in losers_text
    assert "`WINA`" not in losers_text
