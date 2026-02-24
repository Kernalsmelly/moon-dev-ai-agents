"""
Polymarket Micro-Edge Performance Dashboard

A simple CLI dashboard to view:
- Current positions and PnL
- Signal performance by type
- Edge calibration accuracy
- Resolution statistics

Usage:
    python -m src.polymarket.dashboard
    # or
    from src.polymarket.dashboard import print_dashboard
    print_dashboard()
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Database paths
DATA_DIR = Path(__file__).parent.parent / "data" / "polymarket_micro"
MARKETS_DB = DATA_DIR / "niche_markets.db"
PNL_DB = DATA_DIR / "simulated_pnl.db"


def get_signal_stats() -> Dict:
    """Get statistics from signals table."""
    if not MARKETS_DB.exists():
        return {}

    conn = sqlite3.connect(MARKETS_DB)
    conn.row_factory = sqlite3.Row

    stats = {}

    # Total signals by status
    cur = conn.execute('''
        SELECT status, COUNT(*) as count
        FROM signals
        GROUP BY status
    ''')
    stats['by_status'] = {row['status']: row['count'] for row in cur.fetchall()}

    # Signals by type
    cur = conn.execute('''
        SELECT signal_type, COUNT(*) as count, AVG(edge_pct) as avg_edge, AVG(confidence) as avg_conf
        FROM signals
        GROUP BY signal_type
    ''')
    stats['by_type'] = {
        row['signal_type']: {
            'count': row['count'],
            'avg_edge': row['avg_edge'],
            'avg_conf': row['avg_conf']
        } for row in cur.fetchall()
    }

    # Edge distribution
    cur = conn.execute('''
        SELECT
            CASE
                WHEN edge_pct < 10 THEN '0-10%'
                WHEN edge_pct < 20 THEN '10-20%'
                WHEN edge_pct < 30 THEN '20-30%'
                WHEN edge_pct < 40 THEN '30-40%'
                ELSE '40%+'
            END as bucket,
            COUNT(*) as count
        FROM signals
        GROUP BY bucket
        ORDER BY bucket
    ''')
    stats['edge_distribution'] = {row['bucket']: row['count'] for row in cur.fetchall()}

    # Recent signals (last 24h)
    cur = conn.execute('''
        SELECT COUNT(*) as count
        FROM signals
        WHERE created_at > datetime('now', '-1 day')
    ''')
    stats['last_24h'] = cur.fetchone()['count']

    conn.close()
    return stats


def get_pnl_stats() -> Dict:
    """Get PnL statistics from simulated trades."""
    if not PNL_DB.exists():
        return {'message': 'No PnL data yet'}

    conn = sqlite3.connect(PNL_DB)
    conn.row_factory = sqlite3.Row

    stats = {}

    # Overall stats
    cur = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN was_correct = 0 AND resolved = 1 THEN 1 ELSE 0 END) as losses,
            SUM(simulated_pnl_usd) as total_pnl,
            AVG(CASE WHEN resolved = 1 THEN simulated_pnl_usd END) as avg_pnl,
            MAX(simulated_pnl_usd) as best_trade,
            MIN(simulated_pnl_usd) as worst_trade
        FROM simulated_trades
    ''')
    row = cur.fetchone()

    stats['total_trades'] = row['total'] or 0
    stats['resolved'] = row['resolved'] or 0
    stats['pending'] = row['pending'] or 0
    stats['wins'] = row['wins'] or 0
    stats['losses'] = row['losses'] or 0
    stats['total_pnl'] = row['total_pnl'] or 0
    stats['avg_pnl'] = row['avg_pnl'] or 0
    stats['best_trade'] = row['best_trade'] or 0
    stats['worst_trade'] = row['worst_trade'] or 0

    if stats['resolved'] > 0:
        stats['win_rate'] = (stats['wins'] / stats['resolved']) * 100
    else:
        stats['win_rate'] = 0

    # By signal type
    cur = conn.execute('''
        SELECT
            signal_type,
            COUNT(*) as count,
            SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
            SUM(simulated_pnl_usd) as pnl,
            AVG(edge_pct) as avg_edge
        FROM simulated_trades
        GROUP BY signal_type
    ''')
    stats['by_type'] = {}
    for row in cur.fetchall():
        resolved = row['resolved'] or 0
        wins = row['wins'] or 0
        stats['by_type'][row['signal_type'] or 'unknown'] = {
            'count': row['count'],
            'resolved': resolved,
            'wins': wins,
            'win_rate': (wins / resolved * 100) if resolved > 0 else 0,
            'pnl': row['pnl'] or 0,
            'avg_edge': row['avg_edge'] or 0
        }

    # By side (YES vs NO)
    cur = conn.execute('''
        SELECT
            side,
            COUNT(*) as count,
            SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
            SUM(simulated_pnl_usd) as pnl
        FROM simulated_trades
        GROUP BY side
    ''')
    stats['by_side'] = {}
    for row in cur.fetchall():
        resolved = row['resolved'] or 0
        wins = row['wins'] or 0
        stats['by_side'][row['side']] = {
            'count': row['count'],
            'resolved': resolved,
            'wins': wins,
            'win_rate': (wins / resolved * 100) if resolved > 0 else 0,
            'pnl': row['pnl'] or 0
        }

    conn.close()
    return stats


def get_category_breakdown() -> Dict:
    """Get breakdown by market category."""
    if not PNL_DB.exists():
        return {}

    conn = sqlite3.connect(PNL_DB)
    conn.row_factory = sqlite3.Row

    cur = conn.execute('''
        SELECT
            CASE
                WHEN question LIKE '%Oscar%' OR question LIKE '%Academy Award%' OR question LIKE '%nominated%' THEN 'Oscar'
                WHEN question LIKE '%election%' OR question LIKE '%governor%' OR question LIKE '%Senate%' THEN 'Politics'
                WHEN question LIKE '%Bitcoin%' OR question LIKE '%BTC%' OR question LIKE '%crypto%' THEN 'Crypto'
                WHEN question LIKE '%NBA%' OR question LIKE '%NFL%' OR question LIKE '%NHL%' THEN 'Sports'
                ELSE 'Other'
            END as category,
            COUNT(*) as count,
            SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
            SUM(simulated_pnl_usd) as pnl,
            AVG(confidence) as avg_conf
        FROM simulated_trades
        GROUP BY category
        ORDER BY count DESC
    ''')

    categories = {}
    for row in cur.fetchall():
        resolved = row['resolved'] or 0
        wins = row['wins'] or 0
        categories[row['category']] = {
            'count': row['count'],
            'resolved': resolved,
            'wins': wins,
            'win_rate': (wins / resolved * 100) if resolved > 0 else 0,
            'pnl': row['pnl'] or 0,
            'avg_conf': row['avg_conf'] or 0
        }

    conn.close()
    return categories


def get_recent_trades(limit: int = 10) -> List[Dict]:
    """Get most recent simulated trades."""
    if not PNL_DB.exists():
        return []

    conn = sqlite3.connect(PNL_DB)
    conn.row_factory = sqlite3.Row

    cur = conn.execute('''
        SELECT
            id, condition_id, question, side, entry_price,
            simulated_amount_usd, signal_type, edge_pct, confidence,
            resolved, actual_outcome, simulated_pnl_usd, was_correct,
            created_at
        FROM simulated_trades
        ORDER BY created_at DESC
        LIMIT ?
    ''', (limit,))

    trades = [dict(row) for row in cur.fetchall()]
    conn.close()
    return trades


def print_dashboard():
    """Print the performance dashboard to console."""
    print("\n" + "=" * 70)
    print("   POLYMARKET MICRO-EDGE PERFORMANCE DASHBOARD")
    print("=" * 70)
    print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Signal stats
    signal_stats = get_signal_stats()
    if signal_stats:
        print("\n--- SIGNAL GENERATION ---")
        by_status = signal_stats.get('by_status', {})
        total = sum(by_status.values())
        print(f"Total Signals: {total}")
        for status, count in by_status.items():
            print(f"  {status}: {count}")

        print(f"\nLast 24 hours: {signal_stats.get('last_24h', 0)} new signals")

        print("\nEdge Distribution:")
        for bucket, count in signal_stats.get('edge_distribution', {}).items():
            bar = "█" * min(30, count // 50)
            print(f"  {bucket:>8}: {count:>5} {bar}")

    # PnL stats
    pnl_stats = get_pnl_stats()
    if pnl_stats and 'total_trades' in pnl_stats:
        print("\n--- SIMULATED PnL ---")
        print(f"Total Trades: {pnl_stats['total_trades']}")
        print(f"  Pending:  {pnl_stats['pending']}")
        print(f"  Resolved: {pnl_stats['resolved']}")

        if pnl_stats['resolved'] > 0:
            print(f"\nPerformance:")
            print(f"  Win Rate: {pnl_stats['win_rate']:.1f}% ({pnl_stats['wins']}W / {pnl_stats['losses']}L)")
            print(f"  Total PnL: ${pnl_stats['total_pnl']:.2f}")
            print(f"  Avg PnL/Trade: ${pnl_stats['avg_pnl']:.2f}")
            print(f"  Best Trade: ${pnl_stats['best_trade']:.2f}")
            print(f"  Worst Trade: ${pnl_stats['worst_trade']:.2f}")
        else:
            print("\n  [No resolved trades yet - waiting for market outcomes]")

        # By side
        by_side = pnl_stats.get('by_side', {})
        if by_side:
            print("\nBy Side:")
            for side, data in by_side.items():
                wr = f"{data['win_rate']:.0f}%" if data['resolved'] > 0 else "N/A"
                print(f"  {side}: {data['count']} trades, {data['resolved']} resolved, WR: {wr}, PnL: ${data['pnl']:.2f}")

    # Category breakdown
    categories = get_category_breakdown()
    if categories:
        print("\n--- BY CATEGORY ---")
        for cat, data in categories.items():
            wr = f"{data['win_rate']:.0f}%" if data['resolved'] > 0 else "N/A"
            conf = f"{data['avg_conf']*100:.0f}%" if data['avg_conf'] else "N/A"
            print(f"  {cat:>10}: {data['count']:>4} trades, WR: {wr:>5}, Conf: {conf:>4}, PnL: ${data['pnl']:>7.2f}")

    # Recent trades
    recent = get_recent_trades(5)
    if recent:
        print("\n--- RECENT TRADES ---")
        for t in recent:
            status = "✅" if t['was_correct'] else "❌" if t['resolved'] else "⏳"
            pnl = f"${t['simulated_pnl_usd']:.2f}" if t['simulated_pnl_usd'] else "-"
            question = (t['question'] or '')[:45]
            print(f"  {status} {t['side']:>3} @ ${t['entry_price']:.2f} ({t['edge_pct']:.0f}% edge) | {pnl:>7} | {question}...")

    print("\n" + "=" * 70)
    print("   Run 'python -m src.polymarket.dashboard' to refresh")
    print("=" * 70 + "\n")


def export_to_html(filepath: str = None) -> str:
    """Export dashboard to HTML file."""
    filepath = filepath or str(DATA_DIR / "dashboard.html")

    signal_stats = get_signal_stats()
    pnl_stats = get_pnl_stats()
    categories = get_category_breakdown()
    recent = get_recent_trades(20)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Polymarket Micro-Edge Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
        h1, h2 {{ color: #00d4ff; }}
        .card {{ background: #16213e; border-radius: 8px; padding: 20px; margin: 10px 0; }}
        .stat {{ font-size: 2em; font-weight: bold; color: #00d4ff; }}
        .win {{ color: #00ff88; }}
        .loss {{ color: #ff4444; }}
        .pending {{ color: #ffaa00; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #333; }}
        th {{ background: #0f3460; }}
        .bar {{ background: #00d4ff; height: 20px; }}
    </style>
</head>
<body>
    <h1>Polymarket Micro-Edge Dashboard</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

    <div class="card">
        <h2>Overview</h2>
        <p>Total Signals: <span class="stat">{sum(signal_stats.get('by_status', {}).values())}</span></p>
        <p>Pending Trades: <span class="stat pending">{pnl_stats.get('pending', 0)}</span></p>
        <p>Resolved: <span class="stat">{pnl_stats.get('resolved', 0)}</span></p>
    </div>

    <div class="card">
        <h2>Performance</h2>
        <p>Win Rate: <span class="stat">{pnl_stats.get('win_rate', 0):.1f}%</span></p>
        <p>Total PnL: <span class="stat {'win' if pnl_stats.get('total_pnl', 0) >= 0 else 'loss'}">${pnl_stats.get('total_pnl', 0):.2f}</span></p>
    </div>

    <div class="card">
        <h2>By Category</h2>
        <table>
            <tr><th>Category</th><th>Trades</th><th>Resolved</th><th>Win Rate</th><th>PnL</th></tr>
"""

    for cat, data in categories.items():
        wr = f"{data['win_rate']:.0f}%" if data['resolved'] > 0 else "N/A"
        html += f"<tr><td>{cat}</td><td>{data['count']}</td><td>{data['resolved']}</td><td>{wr}</td><td>${data['pnl']:.2f}</td></tr>\n"

    html += """
        </table>
    </div>

    <div class="card">
        <h2>Recent Trades</h2>
        <table>
            <tr><th>Status</th><th>Side</th><th>Entry</th><th>Edge</th><th>PnL</th><th>Market</th></tr>
"""

    for t in recent:
        status = "✅" if t['was_correct'] else "❌" if t['resolved'] else "⏳"
        pnl = f"${t['simulated_pnl_usd']:.2f}" if t['simulated_pnl_usd'] else "-"
        question = (t['question'] or '')[:50]
        html += f"<tr><td>{status}</td><td>{t['side']}</td><td>${t['entry_price']:.2f}</td><td>{t['edge_pct']:.0f}%</td><td>{pnl}</td><td>{question}...</td></tr>\n"

    html += """
        </table>
    </div>
</body>
</html>
"""

    with open(filepath, 'w') as f:
        f.write(html)

    return filepath


if __name__ == "__main__":
    print_dashboard()
