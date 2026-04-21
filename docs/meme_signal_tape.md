# Meme Signal Tape

The project's core data product is:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_launch_signals.jsonl`

This file is the normalized signal tape. Every discovery listener writes candidate events into it, and the bot consumes it in `signal-first` mode.

## Row Contract

Every row is built by:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_signal_schema.py`

Canonical top-level fields:

- `ts`: event timestamp
- `first_seen`: earliest known timestamp for this mint in this source path
- `mint`: token mint
- `score`: source-level rank or urgency score
- `schema_version`
- `metrics`: source-specific feature payload after normalization
- `run_id`
- `source`

Normalization aliases similar fields into a common shape:

- `market_cap_usd`, `market_cap`, `mcap`, `fdv` -> `market_cap`
- `liquidity_usd`, `liquidity`, `liq` -> `liquidity`
- `price_change_5m`, `momentum_5m_pct` -> `price_change_5m`
- `price_change_1h`, `momentum_1h_pct` -> `price_change_1h`

## Source Roles

### `ws_logs`

Producer:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/pump_ws_signal_listener.py`

Role:

- earliest Pump on-chain flow snapshot

Best fields:

- `hits`
- `buys`
- `sells`
- `unique_buyers`
- `net_sol_in`
- `top_buyer_share`
- `buyer_wallets`
- `buy_accel`
- `t_first_sell_s`

Weakness:

- usually no direct `market_cap`, `liquidity`, `pair_age_min`, or price-momentum context at signal time

### `dex_mover`

Producer:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/dex_mover_signal_listener.py`

Role:

- market-state and continuation/breakout context from DexScreener-style pair data

Best fields:

- `market_cap`
- `liquidity`
- `price_change_5m`
- `price_change_1h`
- `hits`
- `buys`
- `sells`
- `net_sol_in`
- `buy_sell_ratio`
- `pair_age_min`
- `mover_pattern`

Important caveat:

- `unique_buyers` is estimated
- `top_buyer_share` is estimated or missing

This source is strong for continuation and breakout ranking, but weak for wallet concentration logic.

### `ds_sidecar`

Producer:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/ds_sidecar_signal_listener.py`

Role:

- auxiliary ranked discovery feed

Best fields:

- same market-state fields as `dex_mover`
- extra sidecar rank fields such as `ds_score`, `ds_breakout_readiness`, `ds_relative_strength`, `ds_risk_score`

Important caveat:

- `unique_buyers` is estimated
- `top_buyer_share` is estimated

This source should be treated as a ranking/enrichment feed, not a direct substitute for on-chain buyer identity.

### `wallet_outlier`

Producer:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/wallet_outlier_signal_listener.py`

Role:

- wallet-alpha overlay on top of earlier signals

Adds:

- `wallet_alpha_score`
- `wallet_alpha_confidence`
- `wallet_alpha_wallet`
- `wallet_alpha_origin`
- `wallet_alpha_signals_n`
- `wallet_alpha_outcomes_n`

This is not a primary discovery source. It is a second-order source that depends on earlier `ws_logs` coverage.

## Bot Consumption

Primary consumer:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`

Flow:

1. `_ingest_launch_signals()` tails the JSONL tape and loads hot mints into memory.
2. `filter_token()` reads the source metrics for a mint.
3. Prequote demand gates reject weak candidates before expensive quote or hydration work.
4. If a candidate passes, the bot hydrates with quote, liquidity, mcap, and sellability context.
5. If it still passes, the bot opens a paper or live position.

Important consequence:

- the quality of the tape determines the quality of everything downstream

If the tape mixes observed and estimated fields without source-aware handling, entry quality degrades quickly.

## Design Rules

These rules should hold going forward:

1. Treat source semantics explicitly.
   `ws_logs` means early flow.
   `dex_mover` means market-state.
   `wallet_outlier` means overlay.

2. Never trust estimated fields as if they were observed.
   In practice:
   - estimated `unique_buyers` should not drive hard wallet logic
   - estimated `top_buyer_share` should not be treated like observed concentration

3. Separate discovery from ranking.
   A source row is not a buy signal. It is a feature snapshot.

4. Build ranking from labeled outcomes.
   The tape is valuable because:
   - signals are written here
   - forward outcomes are labeled later
   - the join between the two creates the training surface for the edge

## Report

Use:

```bash
python3 /Users/nickdavis/MOON\ DEV\ BOT/moon-dev-ai-agents/scripts/meme_signal_tape_report.py
```

Outputs:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/signal_tape_report.json`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/signal_tape_report.md`
