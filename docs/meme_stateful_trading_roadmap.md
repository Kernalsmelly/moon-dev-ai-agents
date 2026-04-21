# Meme Stateful Trading Roadmap

This document defines the product we are actually trying to build.

## Product Shape

The goal is not a hype detector and not a random pump chaser.

The goal is a stateful meme trading engine that can:
- catch names early enough to matter
- track each name through a lifecycle
- upgrade names that keep proving themselves
- punish weakness quickly
- earn the right to go live through paper results

## Target Architecture

1. Discovery
- Collect early market signals from the tape.
- Merge internal listeners with external market context.

2. Lifecycle State
- Maintain one canonical state per coin:
  - emerging
  - watch
  - promote
  - cut
  - matured survivor
  - matured failure
- Record transitions over time, not just snapshots.

3. Decision Engine
- Produce a small set of operator-friendly actions:
  - skip
  - watch
  - starter enter
  - add
  - cut
- Use shape, lifecycle, and external readiness together.

4. Paper Execution
- Simulate starter entries, adds, exits, and protection.
- Track expectancy, big-loser rate, readiness drift, and shape drift.

5. Live Execution
- Remains disabled until paper results earn it.
- When enabled, use tiny size, hard safeguards, and explicit operator review.

## What We Keep

These are the pieces already worth building on.

- Signal collection and labeling
- Lifecycle board
- Decision engine
- Shape and steam-loss logic
- External market-data adapter
- Paper overlays
- Daily scorecard

## What We Simplify

These are the things we should avoid bloating.

- Too many separate rankings with overlapping meanings
- Generic "AI brain" behavior without explicit rules
- New feature piles that do not improve paper expectancy
- Broad retuning based on noisy or degraded windows

## Current Thesis

The current evidence suggests:
- our edge is more believable in trade management than in perfect first-pick prediction
- promote-strong is materially better than generic promote
- observe and cut are useful filters
- external readiness matters
- paper expectancy is still held back by loser size

That means the next wave of work should be focused on economics, not analytics sprawl.

## Near-Term Build Priorities

1. Improve trade economics
- reduce oversized losers
- protect winners sooner
- size weaker setups smaller

2. Judge the clean v2 cohort honestly
- do not mix new behavior with legacy trades
- let the new rules build their own sample

3. Keep the lifecycle engine central
- one board
- one state machine
- one paper scoreboard

4. Only then move toward live execution
- small size
- hard circuit breakers
- explicit readiness checklist

## Readiness Standard Before Live

We should not discuss enabling live trading until most of the following are true:

- clean v2 cohort has at least 10-20 closed trades
- clean v2 expectancy is positive
- big-loser rate is materially lower than the legacy sample
- promote-strong still outperforms generic promote
- observe and cut remain protective
- external readiness continues to filter bad trades
- collection uptime is stable enough that the sample is trustworthy

## Bottom Line

This project should become a disciplined paper-trading bot first.

If the paper engine proves it can:
- find enough tradable names
- manage them through the lifecycle
- keep losers contained
- keep expectancy positive

then it earns a small-size live phase.

That is the path.
