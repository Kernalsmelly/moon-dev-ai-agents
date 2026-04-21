#!/usr/bin/env python3
"""Fetch free external market-data enrichment for current live meme decisions.

This is intentionally pragmatic:
- DexScreener gives pair/liquidity/volume/price-change context.
- Jupiter Lite gives actual route/price-impact context for execution readiness.
- Meteora DLMM API gives optional official pool data when we can resolve a Meteora pool.

The goal is not to create alpha by itself. The goal is to improve the bot's
front-half decision quality and paper-trade safety with real market plumbing.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests

BASE = Path('/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents')
REPORTS = BASE / 'data' / 'meme_reports'
DECISION_JSON = REPORTS / 'meme_decision_tracker.json'
PENDING_JSON = REPORTS / 'pending_maturation_report.json'
OUT_JSON = REPORTS / 'meme_market_data_adapter.json'
OUT_MD = REPORTS / 'meme_market_data_adapter.md'
HISTORY_JSONL = REPORTS / 'meme_market_data_adapter_history.jsonl'

DEX_TOKEN_PAIRS = 'https://api.dexscreener.com/token-pairs/v1/solana/{mint}'
JUP_LITE_QUOTE = 'https://lite-api.jup.ag/swap/v1/quote'
METEORA_POOL = 'https://dlmm.datapi.meteora.ag/pools/{address}'
SOL_MINT = 'So11111111111111111111111111111111111111112'
QUOTE_SIZE_SOL = 0.25
SLIPPAGE_BPS = 100
HTTP_TIMEOUT = 15
MAX_ROWS = 20


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload) + '\n')


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == '':
            return None
        return float(value)
    except Exception:
        return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return 'n/a'
    return f'{value * 100.0:.1f}%'


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return 'n/a'
    return f'{value:.{digits}f}'


def _choose_live_rows(decision: dict[str, Any], pending: dict[str, Any], limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in list(decision.get('live_rows') or []):
        mint = str(row.get('mint') or '')
        if not mint or mint in seen:
            continue
        rows.append({
            'mint': mint,
            'symbol': row.get('symbol') or 'n/a',
            'decision_bucket': row.get('decision_bucket') or 'unknown',
            'decision_grade': row.get('decision_grade') or 'unknown',
            'stage': row.get('stage') or 'unknown',
            'status': row.get('status') or 'unknown',
            'shape_state': row.get('shape_state') or 'unknown',
            'useful_score': _to_float(row.get('useful_score')),
            'persistent_score': _to_float(row.get('persistent_score')),
            'survivor_fit': _to_float(row.get('survivor_fit')),
            'attention_score': _to_float(row.get('attention_score')),
            'source': row.get('source') or 'unknown',
            'regime': row.get('regime') or 'unknown',
        })
        seen.add(mint)
        if len(rows) >= limit:
            return rows

    for row in list(pending.get('pending_rows') or []):
        mint = str(row.get('mint') or '')
        if not mint or mint in seen:
            continue
        rows.append({
            'mint': mint,
            'symbol': row.get('symbol') or 'n/a',
            'decision_bucket': 'pending_only',
            'decision_grade': row.get('promotion_decision') or 'unknown',
            'stage': 'pending_only',
            'status': row.get('promotion_decision') or 'unknown',
            'shape_state': row.get('shape_state') or 'unknown',
            'useful_score': _to_float(row.get('useful_score')),
            'persistent_score': _to_float(row.get('persistent_score')),
            'survivor_fit': None,
            'attention_score': None,
            'source': row.get('signal_source') or 'unknown',
            'regime': row.get('persistence_regime0') or 'unknown',
        })
        seen.add(mint)
        if len(rows) >= limit:
            return rows
    return rows


def _safe_get_json(url: str, *, params: dict[str, Any] | None = None) -> tuple[int | None, Any]:
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        try:
            payload = r.json()
        except Exception:
            payload = None
        return r.status_code, payload
    except Exception:
        return None, None


def _fetch_dex_pairs(mint: str) -> list[dict[str, Any]]:
    status, payload = _safe_get_json(DEX_TOKEN_PAIRS.format(mint=mint))
    if status != 200 or not isinstance(payload, list):
        return []
    return payload


def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    return max(pairs, key=lambda p: float(((p.get('liquidity') or {}).get('usd')) or 0.0))


def _fetch_jupiter_quote(mint: str) -> dict[str, Any] | None:
    status, payload = _safe_get_json(
        JUP_LITE_QUOTE,
        params={
            'inputMint': SOL_MINT,
            'outputMint': mint,
            'amount': str(int(QUOTE_SIZE_SOL * 1e9)),
            'slippageBps': str(SLIPPAGE_BPS),
        },
    )
    if status != 200 or not isinstance(payload, dict):
        return None
    return payload


def _fetch_meteora_pool(pair: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pair:
        return None
    dex_id = str(pair.get('dexId') or '')
    if 'meteora' not in dex_id.lower():
        return None
    address = str(pair.get('pairAddress') or '')
    if not address:
        return None
    status, payload = _safe_get_json(METEORA_POOL.format(address=address))
    if status != 200 or not isinstance(payload, dict):
        return None
    return payload


def _execution_readiness(*, dex_liquidity_usd: float | None, dex_price_change_m5: float | None, jup_price_impact_pct: float | None, jup_ok: bool, meteora_pool_ok: bool, shape_state: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not jup_ok:
        return 'no_route', ['jupiter_quote_failed']

    if dex_liquidity_usd is not None and dex_liquidity_usd < 5000:
        reasons.append('thin_liquidity')
    if jup_price_impact_pct is not None and jup_price_impact_pct > 0.05:
        reasons.append('high_price_impact')
    if dex_price_change_m5 is not None and dex_price_change_m5 > 80.0:
        reasons.append('overheated_5m')
    if shape_state in {'losing_steam', 'blowoff_risk'}:
        reasons.append(f'shape:{shape_state}')

    if reasons:
        if 'thin_liquidity' in reasons:
            return 'thin', reasons
        if 'high_price_impact' in reasons:
            return 'high_impact', reasons
        if 'overheated_5m' in reasons:
            return 'overheated', reasons
        return 'fragile', reasons

    if meteora_pool_ok:
        return 'route_ready_meteora', ['jupiter_ok', 'dex_ok', 'meteora_official']
    return 'route_ready', ['jupiter_ok', 'dex_ok']


def _build_row(base_row: dict[str, Any]) -> dict[str, Any]:
    mint = str(base_row['mint'])
    pairs = _fetch_dex_pairs(mint)
    best_pair = _best_pair(pairs)
    quote = _fetch_jupiter_quote(mint)
    meteora_pool = _fetch_meteora_pool(best_pair)

    dex_liquidity_usd = _to_float(((best_pair or {}).get('liquidity') or {}).get('usd'))
    dex_mcap = _to_float((best_pair or {}).get('marketCap')) or _to_float((best_pair or {}).get('fdv'))
    dex_price_change_m5 = _to_float(((best_pair or {}).get('priceChange') or {}).get('m5'))
    dex_price_change_h1 = _to_float(((best_pair or {}).get('priceChange') or {}).get('h1'))
    dex_volume_m5 = _to_float(((best_pair or {}).get('volume') or {}).get('m5'))
    dex_volume_h1 = _to_float(((best_pair or {}).get('volume') or {}).get('h1'))
    dex_txns_m5 = (((best_pair or {}).get('txns') or {}).get('m5')) or {}
    dex_txns_h1 = (((best_pair or {}).get('txns') or {}).get('h1')) or {}
    pair_created_ms = _to_float((best_pair or {}).get('pairCreatedAt'))
    pair_age_min = None
    if pair_created_ms is not None:
        pair_age_min = max(0.0, (time.time() - (pair_created_ms / 1000.0)) / 60.0)

    quote_ok = isinstance(quote, dict)
    price_impact = _to_float((quote or {}).get('priceImpactPct'))
    route_plan = list((quote or {}).get('routePlan') or [])
    first_route = ((route_plan[0] or {}).get('swapInfo') or {}) if route_plan else {}
    jup_label = first_route.get('label') or 'n/a'
    jup_amm_key = first_route.get('ammKey') or None
    jup_route_count = len(route_plan)
    jup_swap_usd = _to_float((quote or {}).get('swapUsdValue'))

    meteora_ok = isinstance(meteora_pool, dict)
    readiness, readiness_reasons = _execution_readiness(
        dex_liquidity_usd=dex_liquidity_usd,
        dex_price_change_m5=dex_price_change_m5,
        jup_price_impact_pct=price_impact,
        jup_ok=quote_ok,
        meteora_pool_ok=meteora_ok,
        shape_state=str(base_row.get('shape_state') or 'unknown'),
    )

    return {
        **base_row,
        'dex_pairs_count': len(pairs),
        'dex_meteora_pairs': sum(1 for p in pairs if 'meteora' in str(p.get('dexId') or '').lower()),
        'dex_best_dex': (best_pair or {}).get('dexId') or 'n/a',
        'dex_pair_address': (best_pair or {}).get('pairAddress') or None,
        'dex_liquidity_usd': dex_liquidity_usd,
        'dex_market_cap': dex_mcap,
        'dex_price_change_m5': dex_price_change_m5,
        'dex_price_change_h1': dex_price_change_h1,
        'dex_volume_m5': dex_volume_m5,
        'dex_volume_h1': dex_volume_h1,
        'dex_buys_m5': int(dex_txns_m5.get('buys') or 0),
        'dex_sells_m5': int(dex_txns_m5.get('sells') or 0),
        'dex_buys_h1': int(dex_txns_h1.get('buys') or 0),
        'dex_sells_h1': int(dex_txns_h1.get('sells') or 0),
        'dex_pair_age_min': pair_age_min,
        'jupiter_quote_ok': quote_ok,
        'jupiter_price_impact_pct': price_impact,
        'jupiter_route_count': jup_route_count,
        'jupiter_label': jup_label,
        'jupiter_amm_key': jup_amm_key,
        'jupiter_swap_usd_value': jup_swap_usd,
        'meteora_pool_ok': meteora_ok,
        'meteora_pool_address': (meteora_pool or {}).get('address') or None,
        'meteora_pool_tvl': _to_float((meteora_pool or {}).get('tvl')),
        'meteora_pool_current_price': _to_float((meteora_pool or {}).get('current_price')),
        'meteora_dynamic_fee_pct': _to_float((meteora_pool or {}).get('dynamic_fee_pct')),
        'execution_readiness': readiness,
        'execution_reasons': readiness_reasons,
    }


def build_report() -> dict[str, Any]:
    decision = _load_json(DECISION_JSON, {})
    pending = _load_json(PENDING_JSON, {})
    base_rows = _choose_live_rows(decision, pending, limit=MAX_ROWS)
    rows = [_build_row(row) for row in base_rows]

    summary = {
        'rows': len(rows),
        'route_ready': sum(1 for r in rows if r['execution_readiness'] == 'route_ready'),
        'route_ready_meteora': sum(1 for r in rows if r['execution_readiness'] == 'route_ready_meteora'),
        'thin': sum(1 for r in rows if r['execution_readiness'] == 'thin'),
        'high_impact': sum(1 for r in rows if r['execution_readiness'] == 'high_impact'),
        'overheated': sum(1 for r in rows if r['execution_readiness'] == 'overheated'),
        'no_route': sum(1 for r in rows if r['execution_readiness'] == 'no_route'),
        'meteora_seen': sum(1 for r in rows if int(r.get('dex_meteora_pairs') or 0) > 0),
    }

    top = sorted(
        rows,
        key=lambda r: (
            0 if str(r.get('execution_readiness')) in {'route_ready_meteora', 'route_ready'} else 1,
            -float(r.get('survivor_fit') or 0.0),
            -float(r.get('useful_score') or 0.0),
            -float(r.get('dex_liquidity_usd') or 0.0),
        ),
    )[:8]

    return {
        'generated_at': time.time(),
        'summary': summary,
        'rows': rows,
        'top': top,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report['summary']
    lines = [
        '# Meme Market Data Adapter',
        '',
        'Free external execution/liquidity enrichment for the live lifecycle board.',
        '',
        'Sources:',
        '- `DexScreener` for pair/liquidity/volume/price-change context.',
        '- `Jupiter Lite` for actual route and price-impact context.',
        '- `Meteora DLMM API` for official pool state when a Meteora pool resolves directly.',
        '',
        '## Summary',
        '',
        f"- Rows analyzed: `{s['rows']}`",
        f"- Route ready: `{s['route_ready']}`",
        f"- Route ready + Meteora official: `{s['route_ready_meteora']}`",
        f"- Thin liquidity: `{s['thin']}`",
        f"- High impact: `{s['high_impact']}`",
        f"- Overheated: `{s['overheated']}`",
        f"- No Jupiter route: `{s['no_route']}`",
        f"- Meteora pairs seen on Dex: `{s['meteora_seen']}`",
        '',
        '## Top Execution Rows',
        '',
        '| Symbol | Decision | Shape | Readiness | Dex | Liquidity | MCap | Jup Impact | Jup Label | Meteora | Notes |',
        '|---|---|---|---|---|---:|---:|---:|---|---|---|',
    ]
    for row in report.get('top') or []:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('decision_grade')}` | `{row.get('shape_state')}` | `{row.get('execution_readiness')}` | `{row.get('dex_best_dex')}` | "
            f"${_fmt_num(_to_float(row.get('dex_liquidity_usd')), 0)} | ${_fmt_num(_to_float(row.get('dex_market_cap')), 0)} | "
            f"{_fmt_pct(_to_float(row.get('jupiter_price_impact_pct')))} | `{row.get('jupiter_label') or 'n/a'}` | "
            f"`{'yes' if row.get('meteora_pool_ok') else ('seen' if int(row.get('dex_meteora_pairs') or 0) > 0 else 'no')}` | {', '.join(row.get('execution_reasons') or []) or '—'} |"
        )
    lines.append('')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def append_history(report: dict[str, Any], path: Path = HISTORY_JSONL) -> None:
    ts = float(report.get('generated_at') or time.time())
    for row in report.get('rows') or []:
        payload = {
            'ts': ts,
            'mint': row.get('mint'),
            'symbol': row.get('symbol'),
            'decision_bucket': row.get('decision_bucket'),
            'decision_grade': row.get('decision_grade'),
            'stage': row.get('stage'),
            'status': row.get('status'),
            'shape_state': row.get('shape_state'),
            'execution_readiness': row.get('execution_readiness'),
            'execution_reasons': row.get('execution_reasons'),
            'dex_best_dex': row.get('dex_best_dex'),
            'dex_liquidity_usd': row.get('dex_liquidity_usd'),
            'dex_market_cap': row.get('dex_market_cap'),
            'dex_price_change_m5': row.get('dex_price_change_m5'),
            'dex_price_change_h1': row.get('dex_price_change_h1'),
            'jupiter_quote_ok': row.get('jupiter_quote_ok'),
            'jupiter_price_impact_pct': row.get('jupiter_price_impact_pct'),
            'jupiter_label': row.get('jupiter_label'),
            'meteora_pool_ok': row.get('meteora_pool_ok'),
            'dex_meteora_pairs': row.get('dex_meteora_pairs'),
            'useful_score': row.get('useful_score'),
            'persistent_score': row.get('persistent_score'),
            'survivor_fit': row.get('survivor_fit'),
        }
        _append_jsonl(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch live free market-data enrichment.')
    parser.add_argument('--out-json', type=Path, default=OUT_JSON)
    parser.add_argument('--out-md', type=Path, default=OUT_MD)
    args = parser.parse_args()

    report = build_report()
    _write_json(args.out_json, report)
    write_md(args.out_md, report)
    append_history(report)
    print(
        'meme_market_data_adapter: '
        f"rows={report['summary']['rows']} "
        f"ready={report['summary']['route_ready'] + report['summary']['route_ready_meteora']} "
        f"no_route={report['summary']['no_route']}"
    )


if __name__ == '__main__':
    main()
