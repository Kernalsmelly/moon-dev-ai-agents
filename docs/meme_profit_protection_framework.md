# Meme Profit & Protection Framework

This document is the current operating blueprint for the meme research system.

It is intentionally not a trading playbook yet. It is a research-to-execution bridge built from the live signal tape, anchor dataset, and outcome reports.

## Goal

The goal is not:

- find every giant runner
- hold every winner forever
- optimize around rare all-time trades

The goal is:

- identify above-baseline short-horizon winners
- protect quickly because most winners decay
- promote only the rare names that continue to hold up

That means the system should eventually treat these as separate jobs:

1. `useful winner detection`
2. `persistence detection`
3. `promotion after survival`

## Core Facts From The Data

Source reports:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_anchor_baseline_model.md`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_useful_regime_monitor.md`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/winner_persistence_report_24h.md`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/pending_maturation_report.md`
- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/late_slow_persistence_monitor.md`

Current evidence:

1. The useful-winner model has real lift.
   - Validation useful baseline is around `18%`.
   - Top-10 model picks are around `50%` useful precision.

2. Most winners do not persist.
   - Recent `24h` persistence windows are dominated by `short_lived_spike`.
   - Persistent runners are rare and sometimes absent in a given day window.

3. `dex_mover` market-state signals are the main practical source.
   - `ws_logs` still matter for rare early outliers.
   - But current repeatable useful-winner signal is mostly in the `dex_mover` family.

4. Breakout and persistence are different problems.
   - `market_state:breakout` currently produces the best useful-winner selection.
   - Slower regimes such as `late_slow_expansion` are more promising for persistence, but are sample-starved.

5. Pending-maturation tracking is already useful.
   - It correctly shows when a strong-looking name rolls over.
   - It also surfaces the rare names that keep holding.

## Research Lanes

The system should operate as separate research lanes, not a single blended score.

### Lane 1: Useful Winner Lane

Purpose:

- identify names likely to produce a strong first move within the short winner window

Current best source:

- `market_state:breakout`

What counts as success:

- improved useful-winner precision over baseline
- especially in top-ranked slices

Current evidence:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/meme_useful_regime_monitor.md`

Interpretation:

- this is the strongest current lane for eventual fast-profit behavior

### Lane 2: Persistence Lane

Purpose:

- identify names likely to still be healthy hours after the first move

Current best candidate regime:

- `late_slow_expansion`

What counts as success:

- persistent precision above the tiny base rate
- enough matured examples to trust the signal

Current evidence:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/late_slow_persistence_monitor.md`

Interpretation:

- this lane is conceptually right but still sample-starved

### Lane 3: Pending Maturation Lane

Purpose:

- track the earliest-useful winners that have not yet reached the 6h persistence horizon

What counts as success:

- quickly separate:
  - `holding_strong`
  - `fragile_but_green`
  - `fading`

Current evidence:

- `/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/data/meme_reports/pending_maturation_report.md`

Interpretation:

- this lane is already providing practical signal
- it is the best current bridge between short-horizon winner detection and later persistence classification

## Eventual Execution Logic

When execution is reintroduced, it should follow a profit-and-protection design rather than a moonshot design.

### Stage 1: Initial Classification

Every candidate starts in one of two buckets:

- `fast-move candidate`
- `persistence-leaning candidate`

The default assumption should be:

- the coin is temporary until proven otherwise

### Stage 2: Protection-First Response

For fast-move candidates:

- assume the move is fragile
- protect early
- do not assume persistence from the initial breakout alone

Execution implication later:

- the first job is to monetize the move, not to marry it

### Stage 3: Promotion Rules

A name earns more time only if it survives after the first burst.

Promotion evidence should come from:

- still positive at later checkpoints
- retention versus peak still healthy
- no severe collapse after the first useful window
- positive status in the pending-maturation lane

Possible later promotion states:

- `not promoted`
- `promoted to monitored runner`
- `promoted to persistence runner`

### Stage 4: Protection Rules

Protection should happen before trying to maximize upside.

The data suggests the future system should behave like this:

- take the first leg seriously
- assume decay is common
- only leave room for extended holding after survival is demonstrated

This means:

- most names should be treated as short-lived opportunities
- a minority deserve runner logic

## Current Practical Rules

These are research rules now, not live trading rules.

1. Trust the useful-winner lane more than the persistence lane.
   - useful selection has real lift
   - persistence still needs more positives

2. Treat `early_hot_breakout` as high-recall but low-durability.

3. Treat `late_slow_expansion` as the best persistence research bucket, even though sample size is still small.

4. Use the pending-maturation report as the main “alive or dying” view.

5. Avoid overfitting to any single 24h window.
   - persistence is too sparse for that

## What Progress Looks Like

We are making progress if:

1. The useful-winner model continues to beat baseline.
2. The pending-maturation lane keeps correctly separating survivors from collapses.
3. The persistence lane starts accumulating more matured positives.
4. The late-slow regime either confirms or fails with enough sample to stop guessing.

## Current Best Use Of The System

Right now, the system is best used for:

- discovery
- outcome labeling
- useful-winner ranking
- persistence hypothesis testing
- survivor tracking

It is not yet best used for:

- full execution
- live capital deployment
- long-hold confidence

## Next Research Steps

1. Keep collection running continuously.
2. Recheck the pending-maturation cohort repeatedly.
3. Keep useful-winner and persistence lanes separate.
4. Let the persistence sample mature before rewriting the persistence model.
5. Once the persistence lane has enough positives, build promotion rules from actual survival behavior instead of static intuition.
