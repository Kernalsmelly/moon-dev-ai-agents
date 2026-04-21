#!/usr/bin/env python3
"""Summarize where the meme system stands on the path from research to live trading."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

BASE = Path('/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents')
REPORTS = BASE / 'data' / 'meme_reports'
OUT_JSON = REPORTS / 'meme_trading_readiness_report.json'
OUT_MD = REPORTS / 'meme_trading_readiness_report.md'

SCORECARD_JSON = REPORTS / 'meme_daily_scorecard.json'
DECISION_JSON = REPORTS / 'meme_decision_tracker.json'
EXPECTANCY_JSON = REPORTS / 'meme_paper_trade_expectancy_report.json'
RISK_JSON = REPORTS / 'meme_paper_trade_risk_report.json'
MARKET_JSON = REPORTS / 'meme_market_data_adapter.json'


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _pct(v: float | None) -> str:
    if v is None:
        return 'n/a'
    return f'{v*100:.1f}%'


def _grade(score: float) -> str:
    if score >= 0.80:
        return 'strong'
    if score >= 0.60:
        return 'promising'
    if score >= 0.40:
        return 'mixed'
    return 'blocked'


def _component(name: str, score: float, why: list[str], next_step: str) -> dict[str, Any]:
    return {
        'name': name,
        'score': score,
        'grade': _grade(score),
        'why': why,
        'next_step': next_step,
    }


def build_report() -> dict[str, Any]:
    scorecard = _load(SCORECARD_JSON)
    decision = _load(DECISION_JSON)
    expectancy = _load(EXPECTANCY_JSON)
    risk = _load(RISK_JSON)
    market = _load(MARKET_JSON)

    confidence = _to_float((((scorecard.get('confidence') or {}).get('overall') or {}).get('score')))
    summary = scorecard.get('summary') or {}
    sig24 = summary.get('signals_24h') or {}
    winner_rate_24h = _to_float(sig24.get('verified_winner_rate'))
    pending_6h = int((((summary.get('persistence_24h') or {}).get('class_counts') or {}).get('pending_6h')) or 0)

    resolved_stats = decision.get('resolved_stats') or []
    resolved_grade_stats = decision.get('resolved_grade_stats') or []
    promote = next((row for row in resolved_stats if row.get('decision_bucket') == 'promote'), {})
    observe = next((row for row in resolved_stats if row.get('decision_bucket') == 'observe'), {})
    cut = next((row for row in resolved_stats if row.get('decision_bucket') == 'cut'), {})
    promote_strong = next((row for row in resolved_grade_stats if row.get('decision_grade') == 'promote_strong'), {})

    promote_survivor = _to_float(promote.get('survivor_precision'))
    promote_persistent = _to_float(promote.get('persistent_precision'))
    observe_useful = _to_float(observe.get('useful_precision'))
    cut_survivor = _to_float(cut.get('survivor_precision'))
    strong_survivor = _to_float(promote_strong.get('survivor_precision'))
    strong_persistent = _to_float(promote_strong.get('persistent_precision'))

    combined = ((expectancy.get('summary') or {}).get('combined') or {})
    expectancy_pct = _to_float(combined.get('expectancy'))
    winrate = _to_float(combined.get('winrate'))
    avg_win = _to_float(combined.get('avg_win'))
    avg_loss = _to_float(combined.get('avg_loss'))
    closed_total = int(((expectancy.get('summary') or {}).get('closed_total')) or 0)

    risk_summary = risk.get('summary') or {}
    big_loser_rate = _to_float(risk_summary.get('big_loser_rate'))

    by_cohort = expectancy.get('by_cohort') or []
    clean_v2 = next((row for row in by_cohort if str(row.get('cohort') or '').startswith('v2_clean_')), None)
    clean_v2_closed = int(clean_v2.get('n') or 0) if clean_v2 else 0
    clean_v2_expectancy = _to_float(clean_v2.get('expectancy')) if clean_v2 else None

    market_summary = market.get('summary') or {}
    rows_analyzed = int(market_summary.get('rows_analyzed') or market_summary.get('rows') or 0)
    route_ready = int(market_summary.get('route_ready_count') or market_summary.get('route_ready') or 0)
    thin = int(market_summary.get('thin_liquidity_count') or market_summary.get('thin') or 0)
    high_impact = int(market_summary.get('high_impact_count') or market_summary.get('high_impact') or 0)

    discovery_score = min(1.0, max(0.0, (confidence or 0.0) * 0.60 + min((winner_rate_24h or 0.0) / 0.20, 1.0) * 0.40))
    lifecycle_score = 0.85 if pending_6h > 0 else 0.65
    decision_score = max(0.0, min(1.0,
        ((promote_survivor or 0.0) / 0.50) * 0.40 +
        ((strong_survivor or 0.0) / 0.70) * 0.35 +
        (1.0 - min((observe_useful or 0.0) / 0.10, 1.0)) * 0.10 +
        (1.0 - min((cut_survivor or 0.0) / 0.05, 1.0)) * 0.15
    ))

    plumbing_score = 0.0
    if rows_analyzed > 0:
        bad_ratio = (thin + high_impact) / rows_analyzed
        plumbing_score = max(0.0, min(1.0, (route_ready / rows_analyzed) * 0.85 + (1.0 - min(bad_ratio, 1.0)) * 0.15))

    economics_score = 0.0
    if expectancy_pct is not None and big_loser_rate is not None:
        payoff_ratio = None
        if avg_win is not None and avg_loss not in (None, 0.0):
            payoff_ratio = avg_win / abs(avg_loss)
        economics_score = max(0.0, min(1.0,
            min(max((expectancy_pct + 0.05) / 0.15, 0.0), 1.0) * 0.45 +
            min(max((winrate or 0.0) / 0.60, 0.0), 1.0) * 0.15 +
            min(max((payoff_ratio or 0.0) / 1.0, 0.0), 1.0) * 0.15 +
            (1.0 - min(big_loser_rate / 0.50, 1.0)) * 0.25
        ))

    live_readiness_score = max(0.0, min(1.0,
        discovery_score * 0.15 +
        lifecycle_score * 0.15 +
        decision_score * 0.25 +
        plumbing_score * 0.15 +
        economics_score * 0.30
    ))

    components = [
        _component(
            'Discovery',
            discovery_score,
            [
                f'24h confidence is {_pct(confidence)}.',
                f'24h verified winner rate is {_pct(winner_rate_24h)}.',
            ],
            'Keep uptime clean and avoid overfitting low-confidence windows.'
        ),
        _component(
            'Lifecycle State',
            lifecycle_score,
            [
                f'There are {pending_6h} names still pending toward the 6h horizon.',
                'The system already tracks emerging, watch, promote, cut, and matured outcomes.',
            ],
            'Keep the lifecycle board as the state machine and simplify around it.'
        ),
        _component(
            'Decision Engine',
            decision_score,
            [
                f'Promote survivor precision is {_pct(promote_survivor)} and persistent precision is {_pct(promote_persistent)}.',
                f'Promote-strong survivor precision is {_pct(strong_survivor)} and persistent precision is {_pct(strong_persistent)}.',
                f'Observe useful precision is {_pct(observe_useful)}; cut survivor precision is {_pct(cut_survivor)}.',
            ],
            'Keep promote selective and let observe/cut stay hard filters.'
        ),
        _component(
            'External Plumbing',
            plumbing_score,
            [
                f'External adapter analyzed {rows_analyzed} rows.',
                f'Route-ready rows: {route_ready}; thin/high-impact rows: {thin + high_impact}.',
            ],
            'Use readiness as both an entry gate and a deterioration trigger after entry.'
        ),
        _component(
            'Paper Economics',
            economics_score,
            [
                f'Closed paper trades: {closed_total}; expectancy is {_pct(expectancy_pct)}.',
                f'Winrate is {_pct(winrate)} with average win {_pct(avg_win)} and average loss {_pct(avg_loss)}.',
                f'Big loser rate is {_pct(big_loser_rate)}.',
            ],
            'Focus on loser size, profit-locking, and faster deterioration exits until expectancy turns positive.'
        ),
        _component(
            'Live Readiness',
            live_readiness_score,
            [
                f'Overall readiness score is {_pct(live_readiness_score)}.',
                f'Clean v2 cohort closed trades: {clean_v2_closed}; clean cohort expectancy: {_pct(clean_v2_expectancy)}.',
            ],
            'Do not enable live trading until the clean v2 cohort shows positive expectancy with enough sample size.'
        ),
    ]

    strengths = [
        'Lifecycle tracking is real: the system can follow names through stages and not just detect spikes.',
        'Decision quality is real: promote-strong is materially better than generic promote, while observe and cut are doing useful filtering.',
        'External execution readiness is now in the loop, so the paper trader can care about tradability and not just internal excitement.',
    ]
    blockers = [
        'Paper expectancy is still negative, so the system is not economically ready.',
        'Average losses are still too large relative to average wins.',
        'The clean v2 cohort has not produced enough closed trades yet to prove the new rules.',
    ]
    next_milestones = [
        'Grow the clean v2 cohort to at least 10-20 closed trades.',
        'Keep tightening early-deterioration exits and winner protection until expectancy turns positive.',
        'Add confidence-based sizing so weaker setups risk less capital in paper mode.',
        'Only discuss live execution after the clean cohort shows positive expectancy with an acceptable big-loser rate.',
    ]

    return {
        'generated_at': time.time(),
        'architecture': {
            'target_layers': ['Discovery', 'Lifecycle State', 'Decision Engine', 'Paper Execution', 'Live Execution'],
            'current_best_shape': 'Stateful trading engine: catch early, track through the lifecycle, promote strength, cut weakness fast, and earn live deployment through paper results.',
        },
        'components': components,
        'strengths': strengths,
        'blockers': blockers,
        'next_milestones': next_milestones,
        'metrics': {
            'winner_rate_24h': winner_rate_24h,
            'promote_survivor_precision': promote_survivor,
            'promote_strong_survivor_precision': strong_survivor,
            'combined_expectancy': expectancy_pct,
            'big_loser_rate': big_loser_rate,
            'clean_v2_closed': clean_v2_closed,
            'clean_v2_expectancy': clean_v2_expectancy,
        },
    }


def render_md(report: dict[str, Any]) -> str:
    lines = [
        '# Meme Trading Readiness Report',
        '',
        'This report answers one question: how close is the current meme system to becoming a disciplined trading bot rather than just a research stack?',
        '',
        '## Target Shape',
        '',
        '- Discovery',
        '- Lifecycle State',
        '- Decision Engine',
        '- Paper Execution',
        '- Live Execution',
        '',
        f"Current best shape: {report['architecture']['current_best_shape']}",
        '',
        '## Component Scorecard',
        '',
        '| Component | Score | Grade | Why It Matters | Next Step |',
        '|---|---:|---|---|---|',
    ]
    for comp in report['components']:
        lines.append(
            f"| {comp['name']} | {_pct(comp['score'])} | `{comp['grade']}` | {' '.join(comp['why'])} | {comp['next_step']} |"
        )

    lines.extend(['', '## Strengths', ''])
    for item in report['strengths']:
        lines.append(f'- {item}')

    lines.extend(['', '## Blockers', ''])
    for item in report['blockers']:
        lines.append(f'- {item}')

    lines.extend(['', '## Near-Term Milestones', ''])
    for idx, item in enumerate(report['next_milestones'], 1):
        lines.append(f'{idx}. {item}')

    lines.extend([
        '',
        '## Bottom Line',
        '',
        '- The system is already beyond pure research: it has a state machine, a decision engine, and live paper execution.',
        '- The main blocker is no longer idea generation. It is trade economics: loser size is still too large.',
        '- The clean v2 cohort is now the right lens for judging progress toward live trading.',
        '',
    ])
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate a trading-readiness report for the meme system.')
    parser.add_argument('--out-json', type=Path, default=OUT_JSON)
    parser.add_argument('--out-md', type=Path, default=OUT_MD)
    args = parser.parse_args()

    report = build_report()
    args.out_json.write_text(json.dumps(report, indent=2), encoding='utf-8')
    args.out_md.write_text(render_md(report), encoding='utf-8')
    live_component = next((c for c in report['components'] if c['name'] == 'Live Readiness'), {})
    print(f"meme-trading-readiness score={_pct(live_component.get('score'))} grade={live_component.get('grade')}")


if __name__ == '__main__':
    main()
