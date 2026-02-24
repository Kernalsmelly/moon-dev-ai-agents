# Codex Handoff: Solana Meme Token Execution Agent

Repo: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents`

## What Is Running (Pipeline)

Background supervisor:

- `scripts/meme_pipeline_supervisor.py` (spawns and restarts the processes below)

Expected child processes (when WS enabled):

- `scripts/pump_ws_signal_listener.py` (writes launch signals to `data/meme_launch_signals.jsonl`)
- `scripts/signal_outcome_recorder.py` (evaluates outcomes for emitted signals)
- `scripts/meme_pipeline_health.py` (health checks)
- `scripts/meme_edge_reporter.py` + `scripts/meme_edge_decider.py` (threshold tuning loop)
- `src/meme_bot.py` (PAPER trading bot)
- `scripts/meme_hourly_discord_summary.py` (optional summary to Discord)
- `scripts/meme_auto_attribution.py` (periodic run + feature attribution snapshots)

## Key Data Files

- `data/positions.db`: positions + trade history (SQLite)
- `data/meme_launch_signals.jsonl`: launch signals (JSONL)
- `data/meme_launch_features.csv`: early-window feature dataset
- `config/rpc_pool.json`: generated RPC pool (gitignored)
- Logs: `logs/`

## Current Mode / Strategy Shape

- PAPER trading
- Signal-first hybrid discovery: bot consumes `meme_launch_signals.jsonl` and then hydrates microstructure (Dex-style checks) before entering.
- Hard safety constraints in env:
  - Minimum market cap filter (no microcaps)
  - Minimum liquidity filter
  - Global position sizing caps (`MEME_MAX_POSITION_USD`, `MEME_MAX_POSITION_LIQ_PCT`)

## How To Check Current Results (Quick)

- Attribution snapshots: `logs/meme_auto_attribution.log`
- Bot runtime log: `logs/meme_bot_early_edge_auto.log`
- WS discovery log: `logs/pump_ws_signal_listener.log`
- Trades DB:
  - `sqlite3 data/positions.db "select trade_id,symbol,pnl_usd,exit_reason,created_at from trades order by trade_id desc limit 20;"`

## Why Performance Was Poor (Historic)

Losses were dominated by tail events (large single-trade drawdowns) rather than a low win rate alone.
The fix direction is: cap position sizes and enforce strict entry gating, then tune thresholds with attribution.

## Next Engineering Steps (Most Impactful)

1. Let signal-first run until it accumulates enough post-change exits (30 to 50).
2. Use `logs/meme_auto_attribution.log` to pick one lever at a time:
   - If `MAX_LOSS_CAP` dominates: reduce position caps or tighten entry impact limits.
   - If churn dominates: adjust scale-in and confirmation logic.
3. Improve signal quality:
   - Iterate Pump WS gates (buyers/net SOL in/top buyer share/acceleration) based on outcome recorder stats.

## Latest Change (2026-02-11)

- Added prequote score-bypass in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - Very strong explicit demand can pass even if heuristic `score` is slightly below floor.
  - New env controls: `MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_*`.
- Added degraded-emission gate in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/pump_ws_signal_listener.py`:
  - `PUMP_SIGNAL_ALLOW_DEGRADED_EMIT=false` suppresses signals missing demand metrics.
  - Goal: reduce score-noise and downstream API waste.

## Latest Change (2026-02-12)

- Added run-scoped funnel-stall monitoring in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_health.py`:
  - Tracks and prints `run_id`, `last_debug_age_s`, and `last_pass_prequote_age_s`.
  - Auto-detects active `run_id` from bot log so stale prior runs do not contaminate status.
  - Optional Discord alerts for:
    - no new launch signals past threshold
    - discovery alive but no `pass_prequote` for too long
- Enabled health alerts in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/.env`:
  - `MEME_HEALTH_ALERTS_ENABLED=1`
  - `MEME_HEALTH_ALERT_COOLDOWN_S=900`
  - `MEME_HEALTH_MAX_SIGNAL_AGE_S=900`
  - `MEME_HEALTH_MAX_PASS_PREQUOTE_AGE_S=1800`
  - `MEME_HEALTH_AUTO_RUN_ID=1`
- Expanded reject cooldown coverage to stop hot-mint re-evaluation loops:
  - `.env`: `MEME_SIGNAL_REJECT_COOLDOWN_REASONS` now includes prequote/core/liquidity reject classes
  - `src/meme_bot.py`: same expanded list is now the default fallback
- Re-enabled high-throughput WS path:
  - `.env`: `PUMP_WS_USE_BLOCK_SUBSCRIBE=true`
  - `pump_ws_signal_listener` now emits with `tx_calls=0` (WS block payloads), reducing HTTP RPC pressure.
- Added WS mode resilience in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/pump_ws_signal_listener.py`:
  - `PUMP_WS_AUTO_FALLBACK_TO_LOGS=true` auto-falls back from `blockSubscribe` to `logsSubscribe` after repeated stale sessions.
  - Status lines now include `mode=block|logs`.
- Operational tuning based on live behavior:
  - `.env`: kept `PUMP_WS_USE_BLOCK_SUBSCRIBE=false` for now (block mode was repeatedly stale in this session).
  - `.env`: pinned `HELIUS_WS_URLS` to Syndica-only to avoid QuickNode WSS upstream disconnect churn.
- Loosened top-share gates slightly for throughput while keeping concentration controls:
  - `.env`: `PUMP_SIGNAL_MAX_TOP_BUYER_SHARE=0.50`
  - `.env`: `MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE=0.50`
- Removed redundant prequote-time market-cap hard requirement from core-metrics gate:
  - `.env`: `MEME_SIGNAL_CORE_REQUIRE_MCAP_PREQUOTE=false`
  - `src/meme_bot.py`: core-metrics check now allows mcap hydration to happen in later mcap gate.
- Reject-cooldown refinement:
  - `.env`: removed `mcap_low,mcap_missing` from `MEME_SIGNAL_REJECT_COOLDOWN_REASONS` so mcap borderline mints can be rechecked quickly.
  - `src/meme_bot.py`: default reject-cooldown list aligned.
- Latest single-lever prequote adjustment from offline walk-forward:
  - Ran `scripts/meme_prequote_walkforward.py` on local outcomes (`horizon=300`, `lookback=4000`).
  - Output: `data/meme_prequote_walkforward.json` and `data/meme_prequote_walkforward.md`.
  - Recommended change applied in `.env`: `MEME_SIGNAL_PREQUOTE_MIN_HITS=2` (was 3).
  - Bot restarted under supervisor, new run id: `run_1770874496`.

## Test Harness Stabilization (2026-02-12)

- Fixed major class-scope regression in `src/brain.py`:
  - A misplaced dedent had ended `MarketBrain` early and trapped many methods inside a nested helper scope.
  - Restored those methods to class scope (including `_execute_exit_swap`, `_monitor_position_exits`, `_call_birdeye_price`, `_get_token_decimals`, RPC helpers, and Jito bundle sender).
- Fixed monitor loop iteration guard in `src/brain.py`:
  - `iter_count` now increments across loop iterations (was being reset each pass).
- Hardened shutdown in `src/brain.py`:
  - Added short timeouts around client close and task gather to prevent teardown stalls.
- Made exit-logic tests deterministic:
  - `tests/test_exit_logic.py` now instantiates `MarketBrain(..., start_monitor=False)` to avoid background monitor interference in fast-sleep test mode.
- Validation:
  - `pytest -q tests/test_exit_logic.py tests/test_session_stats.py tests/test_rpc_resilience.py`
  - Result: `18 passed`.
  - Replaced deprecated `asyncio.iscoroutinefunction` usage with `inspect.iscoroutinefunction` in `src/brain.py`.

## Latest Change (2026-02-12, reporting normalization)

- Added position-normalized clustering to `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_run_report.py`.
  - New section: `Position-Normalized View`.
  - Clusters leg-level exits (TP0/TP1/trailing/etc) by `mint + (exit_ts - hold_time_sec)` so one position is no longer counted as many independent wins/losses.
  - Fallback clustering for legacy rows missing `hold_time_sec` uses short exit-time gap.
  - New CLI knobs:
    - `--cluster-entry-tolerance-sec` (default `180`)
    - `--cluster-gap-fallback-sec` (default `900`)
- Added tests: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/tests/test_meme_run_report_clusters.py`.
  - Validates reconstructed-entry clustering and fallback behavior.
  - Validation run: `pytest -q tests/test_meme_run_report_clusters.py` -> `2 passed`.
- Current 1h run snapshot with normalization (`run_1770874496`):
  - Leg-level: 9 trades, +$1.05
  - Normalized: 1 position cluster, +$1.05
  - Interpretation: current positive window is still single-symbol concentration (`SHUTDOWN`), not broad edge yet.

## Latest Change (2026-02-12, readiness normalization)

- Extended `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_live_readiness.py` with normalized cluster visibility:
  - Adds `Clusters (normalized)` and `Cluster win rate` to summary.
  - Optional gate: `--min-clusters` to prevent false readiness from multi-leg exits on a tiny symbol set.
  - Added clustering knobs:
    - `--cluster-entry-tolerance-sec` (default `180`)
    - `--cluster-gap-fallback-sec` (default `900`)
- Current 1h check using normalized gate:
  - `python3 scripts/meme_live_readiness.py --hours 1 --auto-run-id --min-clusters 10`
  - Result: `Ready: NO (3/6 gates)`, `Trades=11`, `Clusters=2`, `Tail-loss share=72.3%`.

## Latest Change (2026-02-12, single-lever gate tweak)

- Raised prequote market-cap floor in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/.env`:
  - `MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD=12000` (was `10000`).
- Rationale from normalized run report:
  - Recent losing cluster (`Sovers`) entered around ~$10.1k mcap and contributed most of the drawdown.
- Restarted bot process under supervisor (auto-restarted child):
  - New run id detected: `run_1770876292`.
  - Note: restored open positions may still carry prior run_id in metadata until fresh entries occur.

## Latest Change (2026-02-12, diagnostics + gate consistency)

- Reporting quality improvements in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_run_report.py`:
  - `Top Trades -> Losers` now shows only negative PnL rows (no accidental positive rows when losses are sparse).
  - `Position-Normalized View -> Top cluster losers` now shows only negative clusters.
  - Added concentration diagnostics:
    - `Dominant cluster |abs(PnL)| share`
    - `Dominant cluster leg share`
  - Added `MCap Cohorts` section for quick bucket-level expectancy.
- Added tests for render/cluster behavior:
  - `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/tests/test_meme_run_report_render.py`
  - `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/tests/test_meme_run_report_clusters.py`
  - Validation: `4 passed`.
- Readiness normalization expanded in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_live_readiness.py`:
  - Optional gate `--max-cluster-tail-loss-share` plus summary `Cluster largest-loss share`.
- Fixed status freshness in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_live_status.py`:
  - DB freshness now uses latest mtime across `positions.db` and `positions.db-wal`.
- Improved offline what-if parser in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_gate_whatif.py`:
  - Loads `.env` automatically.
  - Uses fallback keys (`signal_score/score0`, `hits0/buys0/...`, `marketcap0/mcap0`, `top_buyer_share0`) so mixed-history outcome rows are analyzable.
- Gate consistency fix in `.env`:
  - `MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD=12000` (already set)
  - `MEME_SIGNAL_MIN_MCAP_USD=12000` (aligned final gate with prequote gate; was `10000`).
- Bot process restarted under supervisor; new run id observed:
  - `run_1770969031`.

## Latest Change (2026-02-12, while-waiting diagnostics)

- Added run-level cluster diagnostics to `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_live_status.py`:
  - `run_clusters` summary: cluster count, cluster win-rate, avg legs/cluster.
  - Concentration diagnostics: `dom_leg_share` and `dom_abs_pnl_share`.
  - `run_cluster_worst` / `run_cluster_best` sections with legs, pnl, and grouped reasons.
- `run_window` query now includes `mint`, `exit_timestamp`, and parsed metadata for clustering.
- Verified on historical run:
  - `run_1770874496` -> `run_clusters: n=2 wr=50.0% avg_legs=9.00 dom_leg_share=88.9% dom_abs_pnl_share=92.8%`.

## Latest Change (2026-02-13, MVP hardening while waiting on data)

- Added config drift guardrails to `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - New env control: `MEME_CONFIG_GUARDRAILS_MODE` (`off|warn|strict`, default `warn`).
  - Guardrails check and emit on startup for:
    - prequote vs final mcap floor drift
    - unreachable scout-lane floor
    - prequote vs final top-buyer-share drift
    - demand-metrics drift
    - prequote vs final net-sol drift
- Resolved active drift found by guardrails:
  - `.env` aligned `MEME_SIGNAL_MAX_TOP_BUYER_SHARE` to `0.50` (was `0.60`), matching prequote cap.
  - Bot restarted; current run id now `run_1770972389`.
- Added replay regression tool: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_replay_regression_check.py`.
  - Runs baseline + variants via `scripts/meme_replay.py` and compares risk gates:
    - net pnl delta
    - max drawdown delta
    - cluster tail-loss concentration delta
    - dominant cluster leg-share delta
  - Supports strict mode for CI/automation.
- Updated replay engine to support variant mcap bounds in `scripts/meme_replay.py`:
  - `MIN_MARKET_CAP_USD` and `MAX_MARKET_CAP_USD` are now variant-configurable.
- Added reject-mix tuning tool: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_reject_tuning_report.py`.
  - Run-scoped (`--auto-run-id`) reject analytics from `meme_signal_debug.jsonl`.
  - Emits conservative single-lever suggestions from near-threshold reject distributions.
- Replay regression run executed:
  - `python3 scripts/meme_replay_regression_check.py --input data/meme_snapshots.jsonl --out data/meme_replay_regression.csv`
  - Baseline: trades=1656, pnl=-785.45, max_dd=786.04.
  - Current default variants (`mcap_12k`, `mcap_15k`, `mcap_20k`) produced identical results on this snapshot set.
- Reject-tuning run executed (current run):
  - `python3 scripts/meme_reject_tuning_report.py --auto-run-id --minutes 30`
  - Current window is tiny (3 events); no change recommended yet.

## Latest Change (2026-02-13, no-idle automation while waiting for sample)

- Added auto sample-readiness watcher: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_sample_ready_watcher.py`.
  - Polls current `run_id`, computes run-scoped trades + position-cluster metrics.
  - Readiness triggers when all are true:
    - trades >= `MEME_SAMPLE_READY_MIN_TRADES`
    - clusters >= `MEME_SAMPLE_READY_MIN_CLUSTERS`
    - cluster tail-loss share <= `MEME_SAMPLE_READY_MAX_CLUSTER_TAIL`
    - dominant cluster leg share <= `MEME_SAMPLE_READY_MAX_DOMINANT_LEG_SHARE`
  - On trigger, auto-writes bundle to `data/meme_reports/<run_id>/`:
    - `run_report.md`
    - `readiness.md`
    - `reject_tuning.md`
  - Keeps one-time trigger state in `data/meme_sample_ready_state.json`.
- Wired watcher into supervisor in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_supervisor.py`.
  - Enabled via `.env`: `MEME_SAMPLE_READY_WATCHER=1`.
- Added replay regression utility: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_replay_regression_check.py`.
  - Baseline + variants; compares PnL, drawdown, cluster tail-loss, dominant leg share.
- Updated replay filter overrides in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_replay.py`:
  - `MIN_MARKET_CAP_USD`, `MAX_MARKET_CAP_USD` now variant-configurable.
- Added run-scoped reject tuning report: `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_reject_tuning_report.py`.
- Current process status after cleanup/restart:
  - `scripts/meme_pipeline_supervisor.py` running
  - `scripts/meme_sample_ready_watcher.py` running
  - `src/meme_bot.py` running
- Current run id seen by watcher:
  - `run_1770973025`

## Latest Change (2026-02-13, anti-dup + auto heartbeat)

- Added singleton lock in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_supervisor.py`:
  - Uses `logs/meme_pipeline_supervisor.lock` (`flock`) so duplicate supervisors exit immediately.
  - Verified by launching a second supervisor: it prints `another instance is already running; exiting.`
- Enhanced `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_sample_ready_watcher.py`:
  - Adds explicit not-ready reasons (`trades`, `clusters`, `cluster_tail`, `dominant_legs`).
  - Adds periodic heartbeat snapshots even before readiness trigger:
    - `data/meme_reports/<run_id>/sample_status.json`
    - `data/meme_reports/<run_id>/sample_status.md`
- Added `.env` knob:
  - `MEME_SAMPLE_READY_HEARTBEAT_S=1800`
- Current heartbeat artifact confirmed:
  - `data/meme_reports/run_1770974166/sample_status.md`
- Enhanced heartbeat output in sample watcher:
  - `scripts/meme_sample_ready_watcher.py` now writes `reject_tuning_heartbeat.md` on heartbeat snapshots (not only at ready trigger).
- Restart behavior verified:
  - killing `meme_sample_ready_watcher.py` causes supervisor to respawn it with updated code.

## Latest Change (2026-02-14, winner-zone + regime hard brakes)

- Implemented winner-zone gating plumbing in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - New env controls:
    - `MEME_WINNER_ZONE_ENABLED`
    - `MEME_WINNER_ZONE_ENFORCE`
    - `MEME_WINNER_ZONE_BLOCK_WHEN_MISSING`
    - `MEME_WINNER_ZONE_PATH`
    - `MEME_WINNER_ZONE_RELOAD_S`
    - `MEME_WINNER_ZONE_MIN_N`
  - Added zone loader/matcher (`_maybe_reload_winner_zones`, `_winner_zone_match`).
  - Enforced at prequote stage in `filter_token` when enabled (`reject_winner_zone`, `winner_zone_missing`).
  - Added `winner_zone_id` + `winner_zone_objective` to candidate/debug metadata and persisted into position/trade metadata.
- Added hard concentration brakes in regime guard (`_evaluate_entry_regime`) in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - Now run-scoped by default (`MEME_REGIME_SCOPE_RUN_ID=1`).
  - Added cluster concentration checks from recent SELL legs:
    - `MEME_REGIME_CLUSTER_BRAKE_ENABLED`
    - `MEME_REGIME_CLUSTER_MIN_TRADES`
    - `MEME_REGIME_CLUSTER_MIN_CLUSTERS`
    - `MEME_REGIME_CLUSTER_ENTRY_TOLERANCE_SEC`
    - `MEME_REGIME_CLUSTER_GAP_FALLBACK_SEC`
    - `MEME_REGIME_MAX_LOSS_CLUSTER_SHARE`
    - `MEME_REGIME_MAX_DOMINANT_CLUSTER_LEG_SHARE`
  - Regime pause log now reports cluster metrics + trigger reasons.
- Improved winner-zone builder fallback in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_winner_zone_builder.py`:
  - Added automatic coarse-bin fallback when strict/fine segmentation yields zero zones.
  - Added outputs: `selection_mode` in JSON/MD.
- Updated `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_winner_zone_watcher.py` to pass coarse-fallback controls from env.
- Wired winner-zone watcher into `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_pipeline_supervisor.py` behind `MEME_WINNER_ZONE_AUTO_BUILD=1`.
- `.env` updated with winner-zone and regime-cluster settings; main bot keeps zone gating off by default (`MEME_WINNER_ZONE_ENABLED=0`) while A/B lane tests enforcement.
- Runtime actions:
  - Rebuilt zones: `data/meme_winner_zones.json` now generated (current selection mode: `coarse`, zones: 1).
  - Restarted supervisor cleanly; verified running:
    - `scripts/meme_pipeline_supervisor.py`
    - `src/meme_bot.py`
    - `scripts/meme_winner_zone_watcher.py`
  - Started A/B zone runner:
    - `ab_base_1771027231` (zone disabled)
    - `ab_zone_1771027231` (zone enabled/enforced)

## Latest Change (2026-02-14, A/B zone comparison report)

- Added `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py`.
  - Reads active A/B lane metadata from `data/meme_ab_zone_runner.json`.
  - Compares baseline vs winner-zone lane by run_id-scoped trades.
  - Reports:
    - trades, winrate, pnl_usd, avg_pnl
    - cluster_count, loss_cluster_share, dominant_cluster_leg_share
    - zone-tagged trade count
    - top exit reasons
  - Writes markdown output for quick decisioning.
- Generated current report:
  - `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/ab_zone_latest.md`
  - Current sample: both lanes still at 0 closed trades.

## Latest Change (2026-02-14, continuous A/B snapshots)

- Added `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_watcher.py`.
  - Periodically runs `scripts/meme_ab_zone_report.py` and refreshes:
    - `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/ab_zone_latest.md`
- Started watcher as detached process:
  - `scripts/meme_ab_zone_watcher.py` (pid observed: 16156).
- A/B zone lanes remain running:
  - base `ab_base_1771027339`
  - zone `ab_zone_1771027339`

## Latest Change (2026-02-14, while waiting: A/B validity fixes)

- Fixed critical env precedence bug in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - `load_dotenv(..., override=True)` -> `override=False`
  - This preserves subprocess/lane overrides (A/B flags now actually apply).
- Updated `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_runner.py`:
  - Enables per-lane signal debug by default (`MEME_SIGNAL_DEBUG=1`) so we can compare reject mix before enough closed trades.
- Extended `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py`:
  - Adds run-scoped signal-debug section (events, prequote pass rate, top rejects).
- Rebuilt winner zones with more permissive coarse fallback:
  - `data/meme_winner_zones.json` now has 3 coarse zones.
- Quick offline quality check (last ~96h, 120s horizon, 3% roundtrip cost):
  - base: n=1220, win_rate=14.7%, mean_adj=-5.91%
  - zone-matched: n=35, win_rate=65.7%, mean_adj=+4.48%
  - coverage: 2.9%
  - Interpretation: zone filter appears high quality but very selective.

## Latest Change (2026-02-14, controlled zone bypass)

- Implemented controlled winner-zone bypass in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/src/meme_bot.py`:
  - New env knobs:
    - `MEME_WINNER_ZONE_BYPASS_ENABLED`
    - `MEME_WINNER_ZONE_BYPASS_MIN_SIGNAL_SCORE`
    - `MEME_WINNER_ZONE_BYPASS_MIN_HITS`
    - `MEME_WINNER_ZONE_BYPASS_MIN_UNIQUE_BUYERS`
    - `MEME_WINNER_ZONE_BYPASS_MIN_NET_SOL_IN`
    - `MEME_WINNER_ZONE_BYPASS_MIN_MCAP_USD`
    - `MEME_WINNER_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP`
    - `MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE`
  - Added events/metadata:
    - `pass_winner_zone_bypass`
    - `winner_zone_bypassed`, `winner_zone_bypass_reason` persisted into signal debug and trade metadata.
- Refined gate order in prequote pipeline:
  - Winner-zone enforcement now runs **after** baseline prequote checks pass.
  - This avoids zone-gate noise on candidates already failing base prequote filters.
- Updated A/B lane launcher `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_runner.py`:
  - Zone lane now receives dedicated bypass knobs from `MEME_AB_ZONE_*` env settings.
- Updated A/B report `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py`:
  - Adds `zone_bypass_passes` in run-scoped signal-debug table.
- `.env` updated with global and A/B bypass knobs; A/B zone lane currently set to:
  - score>=70, hits>=5, unique>=3, net>=2.0, top_share<=0.45, unknown_mcap allowed.

## Latest Change (2026-02-14, momentum: A/B decision loop)

- Added machine-readable A/B summary output in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py`:
  - writes both markdown + JSON (`ab_zone_latest.md`, `ab_zone_latest.json`).
- Added decider `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_decider.py`:
  - consumes A/B summary JSON
  - outputs recommendation JSON/MD (`ab_zone_decision.json`, `ab_zone_decision.md`)
  - actions: `promote_zone`, `loosen_zone_bypass`, `hold_ab`, `hold_collect`
- Updated `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_watcher.py`:
  - now runs both report + decider each interval.
- Added `.env` knobs for decider thresholds and A/B report JSON/decision output paths.
- Restarted watcher process (new pid observed: `70564`) and verified it writes:
  - `data/meme_reports/ab_zone_latest.md`
  - `data/meme_reports/ab_zone_latest.json`
  - `data/meme_reports/ab_zone_decision.json`
  - `data/meme_reports/ab_zone_decision.md`
- Current decider state:
  - `action=hold_collect`
  - metrics show zone bypass is active (`zone_bypass_passes=2`) while sample is still not trade-sufficient.

## Latest Change (2026-02-14, more progress while A/B sample accumulates)

- Extended A/B report attribution in `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py`:
  - Adds per-lane entry attribution buckets from trade metadata:
    - `zone_match`
    - `zone_bypass`
    - `non_zone`
  - Reports both counts + pnl_usd by bucket in markdown + JSON.
- Added readiness trigger script `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_readiness.py`:
  - Computes `ready`/`reasons_not_ready` from A/B summary and env thresholds.
  - Outputs:
    - `data/meme_reports/ab_zone_ready.json`
    - `data/meme_reports/ab_zone_ready.md`
- Added staged rollout script with rollback guard `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_zone_main_rollout.py`:
  - One-command main-lane promotion path (`MEME_WINNER_ZONE_ENABLED=1`) with:
    - .env backup
    - main-bot restart
    - monitor window
    - automatic rollback if run loss/trade guard is breached.
- Updated `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_apply.py`:
  - `promote_zone` now requires readiness (`ab_zone_ready.json`) when auto-promote is enabled.
  - `promote_zone` path triggers `meme_zone_main_rollout.py` (instead of blind env flip).
- Updated `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_watcher.py`:
  - now runs `report -> decider -> readiness -> apply` each cycle.
- `.env` additions:
  - readiness thresholds (`MEME_AB_ZONE_READY_*`)
  - rollout guard thresholds (`MEME_ZONE_ROLLOUT_*`)
  - output paths for readiness artifacts.
- Current observed A/B status (latest manual run):
  - base trades=1 pnl=-2.70
  - zone trades=1 pnl=-2.70
  - attribution confirms zone lane trade came from `zone_bypass` bucket.
  - readiness currently `False` (insufficient sample + concentration too high at n=1).

## Latest Change (2026-02-14, progress: zone coverage optimizer loop)

- Extended `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_report.py` debug outputs:
  - adds `zone_match_passes` in signal-debug metrics.
  - adds entry attribution section (`zone_match`, `zone_bypass`, `non_zone`) with counts + pnl.
- Extended `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_decider.py`:
  - new action `widen_zone_builder` when bypass flow is strong but true zone matches remain sparse.
- Extended `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/scripts/meme_ab_zone_apply.py`:
  - supports `widen_zone_builder` auto-apply.
  - on apply, rebuilds `data/meme_winner_zones.json` immediately via builder.
- Added new threshold knob in `.env`:
  - `MEME_AB_ZONE_DECIDER_MIN_ZONE_MATCH_PASSES`
- Loop validated end-to-end:
  - decider emitted `widen_zone_builder`
  - apply updated `.env`:
    - `MEME_WINNER_ZONE_COARSE_MIN_SAMPLES=4`
    - `MEME_WINNER_ZONE_COARSE_MIN_WIN_RATE=0.43`
    - `MEME_WINNER_ZONE_COARSE_MIN_MEAN_ADJ=-0.007`
  - builder auto-rebuilt zones (`data/meme_winner_zones.json`).
- New zone set now has broader coverage (6 coarse zones) while maintaining positive zones near top; this is intended to increase true zone-match passes and reduce reliance on bypass-only flow.

## Latest Change (2026-02-14, progress: unknown-mcap zone matching + AB prequote controls)

- Completed and validated unknown-mcap winner-zone matching path:
  - `src/meme_bot.py` now honors `MEME_WINNER_ZONE_MATCH_ALLOW_UNKNOWN_MCAP` during zone-range checks.
  - A/B lane env wiring in `scripts/meme_ab_zone_runner.py` applies `MEME_AB_ZONE_MATCH_ALLOW_UNKNOWN_MCAP` to the zone lane.
- Added A/B-only prequote override wiring in `scripts/meme_ab_zone_runner.py`:
  - maps `MEME_AB_ZONE_PREQUOTE_*` -> `MEME_SIGNAL_PREQUOTE_*` for both A/B lanes (main lane unaffected).
- Added A/B prequote knobs in `.env` for faster sample collection:
  - score floor reduced to `58`, net floor `0.90`, and score-bypass mins relaxed for A/B runs only.
- Restarted A/B lanes and verified new run IDs:
  - base `ab_base_1771032395`
  - zone `ab_zone_1771032395`
- Validation checks:
  - `python3 -m py_compile ...` passed for changed scripts.
  - `ab_zone_latest.json` now shows run-scoped zone-match activity in current run (`zone.debug.zone_match_passes=1`).
  - Signal debug confirms zone pass with unknown mcap (`mcap=0.0`) matching `zone_4`.

## Latest Change (2026-02-14, progress: process dedupe + offline winner signal checks)

- Eliminated duplicate long-running watcher/listener processes that were multiplying API/RPC load:
  - kept supervisor-owned children (PPID=`4247`), removed orphan duplicates (PPID=`1`).
  - duplicate script count reduced to `0`.
- Re-started A/B zone watcher after dedupe:
  - `scripts/meme_ab_zone_watcher.py` running as single process.
- Ran offline winner-signal analysis jobs (no live API dependency):
  - `scripts/meme_pump_dump_commonality.py` produced top separators in `data/meme_pump_dump_commonality.json`.
  - `scripts/meme_prequote_walkforward.py` produced recommendation in `data/meme_prequote_walkforward.json`:
    - `MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN -> 2.00` (for winner bias).
  - `scripts/meme_winner_profile.py --min-group 15` refreshed `data/meme_winner_profile.json`.
- Replay sanity checks on walkforward test snapshots:
  - default replay: `460 trades`, `-233.07 USD`
  - thresholds replay (`data/meme_thresholds_launch.json`): `221 trades`, `-98.36 USD`
  - hot-only replay: `117 trades`, `-49.92 USD`
  - result: still negative offline, but strict filters materially reduced bleed; we should apply winner-bias gates to live paper lanes incrementally, not all at once.

## Latest Change (2026-02-14, progress: AB zone bypass throughput tuning)

- Diagnosed current zone rejects for run `ab_zone_1771032395`:
  - `reject_winner_zone` patterns were mostly score/net outside the single current coarse zone, not mcap gating.
- Tuned A/B zone bypass thresholds in `.env` (A/B lane only):
  - `MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE`: `68 -> 64`
  - `MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN`: `1.80 -> 1.60`
  - `MEME_AB_ZONE_BYPASS_MAX_TOP_BUYER_SHARE`: `0.45 -> 0.50`
- Restarted A/B lanes (fresh run IDs):
  - base `ab_base_1771033133`
  - zone `ab_zone_1771033133`
- Early validation after restart:
  - zone lane now records bypass flow quickly (`zone_bypass_passes=1`) while keeping zone gate active.

## Latest Change (2026-02-14, progress: tri-lane experiment + zone sanity guard + winner gate)

- Implemented tri-lane experiment runner:
  - new script `scripts/meme_ab_zone_tri_runner.py`
  - lanes:
    - `base` (zone off)
    - `match` (zone on, bypass off)
    - `bypass` (zone on, bypass on, direct zone matches suppressed)
- Added bypass-only mode in bot:
  - `src/meme_bot.py` now supports `MEME_WINNER_ZONE_FORCE_BYPASS_ONLY` and logs `winner_zone_match_suppressed` in signal debug.
- Implemented tri-lane reporting for expectancy by entry type:
  - new script `scripts/meme_ab_zone_tri_report.py`
  - outputs:
    - `data/meme_reports/ab_zone_tri_latest.json`
    - `data/meme_reports/ab_zone_tri_latest.md`
  - includes per-lane expectancy for `zone_match`, `zone_bypass`, `non_zone`.
- Implemented continuous tri report watcher:
  - new script `scripts/meme_ab_zone_tri_watcher.py`.
- Added winner-zone file sanity guard:
  - `scripts/meme_winner_zone_watcher.py` now checks zone-count after rebuild, auto-runs fallback rebuild if below min, and writes alert file on degraded/recovered state.
  - env knobs added:
    - `MEME_WINNER_ZONE_SANITY_MIN_ZONES`
    - `MEME_WINNER_ZONE_SANITY_ALERT_PATH`
    - `MEME_WINNER_ZONE_SANITY_COARSE_MIN_*`
- Applied winner-focused A/B prequote gate in `.env`:
  - `MEME_AB_ZONE_PREQUOTE_MIN_SIGNAL_SCORE=60`
  - `MEME_AB_ZONE_PREQUOTE_MIN_NET_SOL_IN=2.00`
  - `MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MIN_NET_SOL_IN=2.00`
- Runtime actions completed:
  - stopped old 2-lane A/B runner.
  - restarted winner-zone watcher with sanity-guard code.
  - started tri lanes with run IDs:
    - `ab_tri_base_1771034986`
    - `ab_tri_match_1771034986`
    - `ab_tri_bypass_1771034986`
  - generated tri report; early debug shows net-sol gate currently dominant reject (`reject_prequote_net`).

## Latest Change (2026-02-14, progress: tri watcher integrated with supervisor)

- Integrated tri watcher into supervisor:
  - `scripts/meme_pipeline_supervisor.py` now supports `MEME_AB_ZONE_TRI_WATCHER=1` and keeps `scripts/meme_ab_zone_tri_watcher.py` alive.
- Enabled `MEME_AB_ZONE_TRI_WATCHER=1` in `.env`.
- Started tri lanes and validated tri report artifacts are refreshing via supervisor-managed watcher.
- Current early tri signal mix under winner-focused prequote gate (`score>=60`, `net>=2.0`):
  - all lanes currently dominated by `reject_prequote_net` and `pass_prequote=0` in first samples.
  - This confirms the stricter winner gate is actively filtering; next decision is whether to hold for cleaner samples or relax net threshold slightly for throughput.

## Latest Change (2026-02-14, progress: tri execution cleanup + throughput tweaks)

- Tri experiment showed early losses were dominated by `SCALE_IN_ABORT`; I disabled scale-in for tri lanes only via `MEME_AB_TRI_SCALE_IN_ENABLED=0` and runner override.
- Added tri bypass-specific thresholds in `.env` and runner wiring (`MEME_AB_TRI_BYPASS_*`) to tune bypass lane independently.
- Relaxed tri prequote mcap floor from `12000` to `10000` for more coverage while keeping score/net winner gates in place.
- Restarted tri lanes on new run `ab_tri_*_1771039069`.
- Current blocker is input cadence: latest launch-signal age ~200s, so lanes are idle waiting for new flow.

## Latest Change (2026-02-14, overnight run configuration)

- Restarted supervisor cleanly so latest watcher code is active; confirmed single active instances for pipeline + tri watcher.
- Disabled legacy 2-lane A/B watcher (`MEME_AB_ZONE_REPORT_WATCHER=0`) to avoid overnight auto-apply interference while tri test runs.
- Improved launch-signal throughput at source: `PUMP_WS_MAX_TX_PER_SEC=4`, `PUMP_SIGNAL_MIN_HITS=2`, `PUMP_SIGNAL_MAX_TOP_BUYER_SHARE=0.60`; restarted pump WS listener under supervisor.
- Tuned tri gates for overnight sample collection:
  - winner-profile disabled in tri lanes (`MEME_AB_TRI_WINNER_PROFILE_ENABLED=0`)
  - scale-in disabled in tri lanes (`MEME_AB_TRI_SCALE_IN_ENABLED=0`)
  - prequote net floor lowered to `1.40`
  - prequote top-share cap raised to `0.60`
  - tri final top-share cap raised to `0.65`
- Restarted tri lanes with run IDs:
  - `ab_tri_base_1771093986`
  - `ab_tri_match_1771093986`
  - `ab_tri_bypass_1771093986`


## Latest Change (2026-02-14, tri conversion + anti-looping)

- Added **exponential market-cap recheck backoff** in `src/meme_bot.py`:
  - new envs: `MEME_SIGNAL_MCAP_RECHECK_BACKOFF_ENABLED`, `MEME_SIGNAL_MCAP_RECHECK_MAX_S`
  - repeated `reject_mcap_low`/`reject_mcap_missing` now progressively delay re-eval per mint instead of fixed 20s loops.
- Added tri-lane **liquidity requirement override** in `scripts/meme_ab_zone_tri_runner.py`:
  - `MEME_AB_TRI_REQUIRE_LIQUIDITY` wires to `MEME_SIGNAL_REQUIRE_LIQUIDITY` for tri runs only.
- Tri report now includes top reject reason list in markdown (`scripts/meme_ab_zone_tri_report.py`).
- Updated `.env` tri parameters:
  - `MEME_AB_TRI_FINAL_MIN_MCAP_USD=10000`
  - `MEME_AB_ZONE_PREQUOTE_MIN_SIGNAL_SCORE=56`
  - `MEME_AB_ZONE_PREQUOTE_MIN_MCAP_USD=10000`
  - `MEME_AB_TRI_REQUIRE_LIQUIDITY=0`
  - `MEME_SIGNAL_MCAP_RECHECK_BACKOFF_ENABLED=1`
  - `MEME_SIGNAL_MCAP_RECHECK_MAX_S=300`
- Restarted tri lanes:
  - `ab_tri_base_1771098266`
  - `ab_tri_match_1771098266`
  - `ab_tri_bypass_1771098266`
- Early post-change tri report snapshot (short window) showed improved prequote pass counts but still no closes yet; primary rejects are now a mix of `liq_missing_signal`, `mcap_low`, and lane-specific `winner_zone`.

## Latest Change (2026-02-14, tri sparsity gating)

- Added tri-only override for core-metrics gate in `scripts/meme_ab_zone_tri_runner.py`:
  - `MEME_AB_TRI_REQUIRE_CORE_METRICS` -> `MEME_SIGNAL_REQUIRE_CORE_METRICS`
- Set `.env` `MEME_AB_TRI_REQUIRE_CORE_METRICS=0` for the tri experiment run to avoid false rejects caused by missing `metrics.liquidity` in launch-signal payloads.
- Restarted tri lanes with run IDs:
  - `ab_tri_base_1771098457`
  - `ab_tri_match_1771098457`
  - `ab_tri_bypass_1771098457`

## Latest Change (2026-02-14, bottleneck pass)

- Found and fixed active blocker: pipeline source was stale because supervisor/listener were not running.
  - restarted supervisor in background
  - confirmed `pump_ws_signal_listener` alive and appending new lines to `data/meme_launch_signals.jsonl`.
- Tri bottleneck reductions:
  - `scripts/meme_ab_zone_tri_runner.py`: tri override for `MEME_SIGNAL_HYBRID_DEX` and tri demand-gate overrides (`MEME_SIGNAL_MIN_BUYS`, `MEME_SIGNAL_MIN_UNIQUE_BUYERS`, `MEME_SIGNAL_MIN_NET_SOL_IN`).
  - `.env`: `MEME_AB_TRI_SIGNAL_HYBRID_DEX=0`, `MEME_AB_TRI_SIGNAL_MIN_BUYS=2`, `MEME_AB_TRI_SIGNAL_MIN_UNIQUE_BUYERS=2`, `MEME_AB_TRI_SIGNAL_MIN_NET_SOL_IN=0.80`.
  - `.env`: `MEME_AB_ZONE_PREQUOTE_MIN_SIGNAL_SCORE=54`, `MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MAX_TOP_BUYER_SHARE=0.60`.
  - `.env`: tri bypass relaxed to reduce zone starvation: `MEME_AB_TRI_BYPASS_MIN_SIGNAL_SCORE=56`, `MEME_AB_TRI_BYPASS_MIN_UNIQUE_BUYERS=2`, `MEME_AB_TRI_BYPASS_MIN_NET_SOL_IN=1.20`, `MEME_AB_TRI_BYPASS_MIN_MCAP_USD=10000`.
- Robustness fix:
  - `src/meme_bot.py` `_ingest_launch_signals` now rewinds offset if launch-signal file is truncated/rotated and offset ends up beyond EOF.
- Current tri run:
  - `ab_tri_base_1771102866`
  - `ab_tri_match_1771102866`
  - `ab_tri_bypass_1771102866`
- Latest reject mix shifted from liquidity/score to stricter structural gates:
  - base: mostly `reject_mcap_low` (expected with 10k floor)
  - match/bypass: mostly `reject_winner_zone` (zone selectivity now dominant bottleneck)
