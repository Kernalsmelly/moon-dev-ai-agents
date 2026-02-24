# Codex Handoff: Solana Meme Bot

## Current Blocker
- This Codex desktop sandbox has no outbound DNS/network access.
- External APIs (Birdeye, DexScreener) and any HTTP fail with ENOTFOUND.
- Because of that, the early-window tracker does not emit signals and the bot cannot trade.

## What Was Built
- WS event ingestion from Solana programs (Pump.fun, Raydium, LaunchLab).
- Event pipeline emits:
  - `data/meme_launch_candidates.jsonl`
  - `data/meme_launch_mints.jsonl`
- Early-window tracker reads mints and emits signals to:
  - `data/meme_launch_signals.jsonl`
- Bot reads signals and only trades when a fresh signal appears.
- Risk controls added: max entries/hour, max loss/hour, loss halt window.
- Signal metadata (score, tier) recorded in trade metadata.
- Reporting:
  - `scripts/meme_signal_monitor.py`
  - `scripts/meme_trade_report.py`
  - `scripts/meme_signal_outcome_report.py`
  - `scripts/meme_monitor_loop.py` runs all of the above.

## Key Files
- Bot: `src/meme_bot.py`
- Tracker: `scripts/meme_early_window_tracker.py` (now supports Birdeye and DexScreener)
- Event pipeline: `scripts/meme_event_pipeline.py`
- WS listener: `scripts/helius_ws_listener.py`
- Config: `config/meme_early_edge_auto.json`

## Current Thresholds (Aggressive)
File: `config/meme_early_edge_auto.json`
- `MIN_VHI_SCORE`: 35
- `MIN_TXNS_5M`: 6
- `MIN_BUYS_5M`: 3
- `MAX_5M_PUMP`: 40

## Expected Env Vars
- `BIRDEYE_API_KEY` (required for tracker in current setup)
- RPC/WS endpoints for Solana (Chainstack WS used previously)

## Startup Commands (network-enabled environment)
1. WS listener
```
nohup python3 scripts/helius_ws_listener.py --programs-file config/helius_programs.json > logs/helius_ws_listener.log 2>&1 &
```
2. Event pipeline
```
nohup python3 scripts/meme_event_pipeline.py --interval 20 > logs/meme_event_pipeline.log 2>&1 &
```
3. Early-window tracker (Birdeye)
```
nohup env BIRDEYE_API_KEY=YOUR_KEY python3 scripts/meme_early_window_tracker.py --mints data/meme_launch_mints.jsonl --out data/meme_launch_signals.jsonl --window-sec 120 --poll 3 --min-score 1.8 > logs/meme_early_window_tracker.log 2>&1 &
```
4. Bot
```
nohup env MEME_CONFIG_FILE=config/meme_early_edge_auto.json \
  MEME_LAUNCH_MINTS_FILE=data/meme_launch_mints.jsonl \
  MEME_LAUNCH_SIGNALS_FILE=data/meme_launch_signals.jsonl \
  MEME_LAUNCH_SIGNAL_TTL=600 \
  MEME_LAUNCH_SIGNAL_IGNORE_HISTORY=true \
  MEME_LAUNCH_SIGNAL_COOLDOWN=900 \
  MEME_MAX_ENTRIES_PER_HOUR=10 \
  MEME_MAX_LOSS_PER_HOUR=10 \
  MEME_LOSS_HALT_SECONDS=1800 \
  MEME_DISCORD_ALERTS=false \
  python3 src/meme_bot.py > logs/meme_bot_early_edge_auto.log 2>&1 &
```
5. Monitor loop
```
nohup python3 scripts/meme_monitor_loop.py --interval 600 > logs/meme_monitor_loop.log 2>&1 &
```

## Notes
- Signals currently do not update in this sandbox because outbound DNS is blocked.
- To proceed, use a network-enabled environment and re-run the commands above.

