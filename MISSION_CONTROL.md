# 🚀 MISSION CONTROL: Moon Dev Trading Bot

## Status

| Field | Value |
|---|---:|
| Current Status | OPERATIONAL |
| Build Version | v1.2.0 |
| Test Coverage | 101/101 Passed |
| Daily Goal | $200.00 / Day |

## Technical Stack

- Python 3.11
- Jito (gated; stubbed in tests)
- Pytest guards / pytest-asyncio
- Async lifecycle (explicit `start()` / `stop()`)

-## Active Roadmap (Todo List)

- [x] Implement 50/50 Moon Bag Logic (Highest Priority)
- [x] Implement Dynamic Slippage (VHI-based)

## COMPLETED

- [x] Structured Logging (data/trades.jsonl)
- [x] Jito Status Sentinel (bundle monitoring & fan-out fallback)

## Next Phase

- [ ] RPC Load Balancer & Failover (moved to top of next phase)
 - [x] RPC Load Balancer & Failover (COMPLETED)
- [ ] Multi-Stage Take Profit (TP)
- [ ] RPC Load Balancer & Failover
- [ ] Multi-Stage Take Profit (TP)
 - [ ] Jito Bundle Integration

## ACTIVE PHASE

- Strategy Tuning

## Risk Guardrails

- Trailing Stop: 5%
- Arming Threshold: 10%
- Jito: Stubbed (set `USE_REAL_JITO=1` to enable upstream client)

---

Note: `data/trades.jsonl` is now the canonical source of truth for trades, PnL, and bundle tracking. This file should be used for programmatic analysis and reporting.
