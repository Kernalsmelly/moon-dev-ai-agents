# Meme Bot Go-Live Checklist

Last updated: 2026-02-11 (local)

## Scope
- This checklist is for moving from PAPER to tiny canary live.
- It is intentionally strict: no live deployment until all critical gates pass.

## Critical Gates (Must Pass)
- [ ] Data continuity:
  - Launch-signal ingestion is continuous for >= 72h (no prolonged stalls).
  - Outcome recorder remains current (no growing pending backlog for prolonged windows).
- [ ] Run-scoped sample size:
  - >= 150 realized SELL exits on a stable config window.
- [ ] Run-scoped profitability:
  - Net PnL >= 0 after conservative cost assumptions.
- [ ] Risk stability:
  - Max drawdown <= configured bound.
  - Largest-loss concentration <= 35% of total losing PnL.
- [ ] Tail-loss control:
  - No dominant catastrophic exit mode (e.g., repeated `MAX_LOSS_CAP` cluster).

## Canary Gates (Required Before Enabling `MEME_LIVE_ENABLED=true`)
- [ ] Position limits set to minimal canary size (hard USD and liquidity caps).
- [ ] Exchange/rpc failure paths tested (forced provider failures still preserve safety exits).
- [ ] Alerting active for:
  - no new signals
  - no prequote passes
  - repeated WS/reconnect degradation
- [ ] Kill-switch tested:
  - `MEME_LIVE_ENABLED=false` stops live entries immediately.

## Rollout Plan
1. Canary live for 24h with minimal size.
2. Review run-scoped PnL + tail-loss profile.
3. If stable, extend to 72h at same size.
4. Increase size in fixed steps only after each stable window.

## Abort Conditions
- Any unexpected tail-loss cluster.
- Any sustained data stall.
- Any mismatch between expected and observed execution behavior.

