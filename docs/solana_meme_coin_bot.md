# Solana Meme Coin Bot

This is the clearest entry point for the **stateful Solana meme coin bot** inside the larger `moon-dev-ai-agents` repo.

The goal is not a hype detector or a generic scanner. The goal is a stateful paper-trading system that can:

- ingest Solana meme-coin signals continuously
- label and track each name through a lifecycle
- decide `observe / watch / promote / cut`
- simulate starter entries, adds, and exits in paper mode
- only move toward live trading after paper results earn it

## Where It Lives

Main runtime and operator scripts:

- Runtime supervisor: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_supervisor.py`
- Operator dashboard: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_daily_scorecard.py`
- Lifecycle board builder: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_lifecycle_monitor.py`
- Decision engine: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_decision_tracker.py`
- External market adapter: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_market_data_adapter.py`
- Main paper trader: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_decision_paper_overlay_v2.py`
- Active paper trader: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_decision_paper_overlay_active.py`
- Operator action board: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_operator_action_board.py`
- Paper expectancy scoreboard: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_paper_trade_expectancy_report.py`

Main live collectors:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/dex_mover_signal_listener.py`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/pump_ws_signal_listener.py`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/raydium_pool_ws_listener.py`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/wallet_outlier_signal_listener.py`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/signal_outcome_recorder.py`

## Main Reports

- Daily scorecard: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_daily_scorecard.md`
- Lifecycle board: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_lifecycle_monitor.md`
- Pending maturation: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/pending_maturation_report.md`
- Decision tracker: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_decision_tracker.md`
- Market data adapter: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_market_data_adapter.md`
- V2 paper overlay: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_decision_paper_overlay_v2_report.md`
- Active paper overlay: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_decision_paper_overlay_active_report.md`
- Operator action board: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_operator_action_board.md`
- Paper expectancy: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_paper_trade_expectancy_report.md`
- Trading readiness: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_trading_readiness_report.md`

## How It Works

The current architecture is:

1. Discovery
- Collect early signals from Dex mover, Pump.fun WS, Raydium WS, and wallet outlier activity.

2. Labeling
- Record whether a name became a useful winner, a survivor, a persistent runner, or failed.

3. Lifecycle state
- Move each symbol through:
  - `emerging_watchlist`
  - `pending_watch`
  - `pending_promote_now`
  - `pending_cut_bias`
  - `matured_survivor`
  - `matured_failed`

4. Decision layer
- Convert the lifecycle state into:
  - `observe`
  - `watch`
  - `promote`
  - `cut`

5. Paper trading
- Simulate the stricter `v2` trade engine with starter entries, confirmation adds, and protection exits.

## Current Runtime

The meme bot runtime is now intended to be owned by:

- LaunchAgent: `/Users/nickdavis/Library/LaunchAgents/com.moondev.meme-pipeline.plist`

That LaunchAgent starts:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_supervisor.py`

The supervisor keeps the collectors and paper traders alive.

The machine-awake helper is:

- `/Users/nickdavis/Library/LaunchAgents/com.moondev.caffeinate.plist`

## Common Commands

Refresh the dashboard manually:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_daily_scorecard.py' --refresh
```

Run the paper trader once:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_decision_paper_overlay_v2.py' --no-refresh
```

Run the active paper trader once:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_decision_paper_overlay_active.py' --no-refresh
```

Rebuild the paper expectancy report:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_paper_trade_expectancy_report.py'
```

Build the operator action board:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_operator_action_board.py'
```

Run the ingestion audit:

```bash
python3 '/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ingestion_audit.py'
```

Inspect the LaunchAgent:

```bash
launchctl print gui/$(id -u)/com.moondev.meme-pipeline
```

## What “Healthy” Looks Like

The system is healthy when:

- all five collectors show `running` in the daily scorecard
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_ingestion_audit.md` shows no `down` or `degraded` sources
- the daily scorecard has non-zero `6h` / `24h` signal flow
- the lifecycle board has current live rows
- the market data adapter has route-readiness rows
- the paper overlay is producing fresh open/closed trade events

## What This Is Not Yet

This bot is **not** live-trading ready yet.

It is currently:

- a working ingestion + labeling + lifecycle + decision + paper-trading stack
- still blocked mainly by paper-trade economics
- especially by loser size and negative expectancy

See:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/docs/meme_stateful_trading_roadmap.md`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_trading_readiness_report.md`
