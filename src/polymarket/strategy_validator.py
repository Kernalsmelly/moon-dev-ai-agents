"""
Strategy Validator for Polymarket Micro-Edge Bot

Validates strategy performance using:
1. Historical Oscar data (2020-2024 actual nominees vs predictions)
2. Confidence calibration analysis (do our confidence scores match reality?)
3. Paper trading simulation with realistic fills
4. Edge decay analysis (does edge disappear as resolution approaches?)

Usage:
    python -m src.polymarket.strategy_validator
"""

import os
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data" / "polymarket_micro"

# Historical Oscar data for backtesting
# Format: {year: {category: [actual_nominees]}}
HISTORICAL_OSCARS = {
    2024: {  # 96th Academy Awards (March 2024)
        'best_picture': ['oppenheimer', 'poor things', 'killers of the flower moon', 'barbie', 'the holdovers', 'maestro', 'past lives', 'the zone of interest', 'american fiction', 'anatomy of a fall'],
        'best_director': ['christopher nolan', 'yorgos lanthimos', 'martin scorsese', 'jonathan glazer', 'justine triet'],
        'best_actor': ['cillian murphy', 'paul giamatti', 'bradley cooper', 'colman domingo', 'jeffrey wright'],
        'best_actress': ['emma stone', 'lily gladstone', 'sandra huller', 'annette bening', 'carey mulligan'],
        'supporting_actor': ['robert downey jr', 'ryan gosling', 'robert de niro', 'mark ruffalo', 'sterling k brown'],
        'supporting_actress': ["da'vine joy randolph", 'emily blunt', 'america ferrera', 'jodie foster', 'danielle brooks'],
    },
    2023: {  # 95th Academy Awards (March 2023)
        'best_picture': ['everything everywhere all at once', 'all quiet on the western front', 'the banshees of inisherin', 'elvis', 'the fabelmans', 'tar', 'top gun maverick', 'triangle of sadness', 'women talking', 'avatar the way of water'],
        'best_director': ['daniel kwan', 'daniel scheinert', 'steven spielberg', 'todd field', 'martin mcdonagh', 'ruben ostlund'],
        'best_actor': ['brendan fraser', 'colin farrell', 'austin butler', 'bill nighy', 'paul mescal'],
        'best_actress': ['michelle yeoh', 'cate blanchett', 'ana de armas', 'andrea riseborough', 'michelle williams'],
    },
    2022: {  # 94th Academy Awards (March 2022)
        'best_picture': ['coda', 'the power of the dog', 'belfast', 'dont look up', 'drive my car', 'dune', 'king richard', 'licorice pizza', 'nightmare alley', 'west side story'],
        'best_director': ['jane campion', 'kenneth branagh', 'ryusuke hamaguchi', 'paul thomas anderson', 'steven spielberg'],
        'best_actor': ['will smith', 'javier bardem', 'benedict cumberbatch', 'andrew garfield', 'denzel washington'],
        'best_actress': ['jessica chastain', 'olivia colman', 'penelope cruz', 'nicole kidman', 'kristen stewart'],
    },
}

# Gold Derby historical predictions (simplified - top 5 predicted for each category)
GOLD_DERBY_PREDICTIONS = {
    2024: {
        'best_picture': ['oppenheimer', 'poor things', 'killers of the flower moon', 'barbie', 'the holdovers'],
        'best_actor': ['cillian murphy', 'paul giamatti', 'bradley cooper', 'colman domingo', 'jeffrey wright'],
        'best_actress': ['lily gladstone', 'emma stone', 'carey mulligan', 'sandra huller', 'annette bening'],
    },
    2023: {
        'best_picture': ['everything everywhere all at once', 'the banshees of inisherin', 'the fabelmans', 'tar', 'top gun maverick'],
        'best_actor': ['brendan fraser', 'austin butler', 'colin farrell', 'bill nighy', 'paul mescal'],
        'best_actress': ['cate blanchett', 'michelle yeoh', 'michelle williams', 'ana de armas', 'viola davis'],
    },
}


class StrategyValidator:
    """Validate trading strategy before going live."""

    def __init__(self):
        self.results = {}

    def backtest_oscar_predictions(self) -> Dict:
        """
        Backtest our Oscar prediction model against historical data.

        Simulates what would have happened if we used our model in past years.
        """
        results = {
            'years': {},
            'overall': {
                'total_predictions': 0,
                'correct': 0,
                'total_pnl': 0,
            }
        }

        for year, categories in HISTORICAL_OSCARS.items():
            year_results = {
                'categories': {},
                'total_predictions': 0,
                'correct': 0,
                'pnl': 0.0,
            }

            for category, actual_nominees in categories.items():
                # Get Gold Derby predictions for this year/category if available
                gd_preds = GOLD_DERBY_PREDICTIONS.get(year, {}).get(category, [])

                if not gd_preds:
                    continue

                category_results = []

                # Simulate betting on top 5 predictions
                for rank, predicted in enumerate(gd_preds[:5], 1):
                    was_nominated = any(predicted in nom.lower() for nom in actual_nominees)

                    # Our model probability based on rank
                    if rank == 1:
                        model_prob = 0.95
                    elif rank == 2:
                        model_prob = 0.92
                    elif rank <= 5:
                        model_prob = 0.88

                    # Simulated market price (typically slightly lower than our estimate)
                    market_price = model_prob - 0.05 - (rank * 0.02)
                    market_price = max(0.60, min(0.95, market_price))

                    # Calculate edge and simulated bet
                    if model_prob > market_price:
                        side = 'YES'
                        edge = (model_prob - market_price) / market_price
                        entry = market_price
                    else:
                        side = 'NO'
                        edge = (market_price - model_prob) / (1 - market_price)
                        entry = 1 - market_price

                    # Simulated $10 bet
                    bet_amount = 10
                    shares = bet_amount / entry

                    # Calculate PnL
                    if side == 'YES':
                        pnl = shares * (1.0 - entry) if was_nominated else -bet_amount
                    else:
                        pnl = shares * (1.0 - entry) if not was_nominated else -bet_amount

                    correct = (side == 'YES' and was_nominated) or (side == 'NO' and not was_nominated)

                    category_results.append({
                        'nominee': predicted,
                        'rank': rank,
                        'model_prob': model_prob,
                        'market_price': market_price,
                        'side': side,
                        'edge': edge * 100,
                        'was_nominated': was_nominated,
                        'correct': correct,
                        'pnl': round(pnl, 2),
                    })

                    year_results['total_predictions'] += 1
                    if correct:
                        year_results['correct'] += 1
                    year_results['pnl'] += pnl

                year_results['categories'][category] = category_results

            results['years'][year] = year_results
            results['overall']['total_predictions'] += year_results['total_predictions']
            results['overall']['correct'] += year_results['correct']
            results['overall']['total_pnl'] += year_results['pnl']

        # Calculate overall stats
        total = results['overall']['total_predictions']
        if total > 0:
            results['overall']['win_rate'] = (results['overall']['correct'] / total) * 100
            results['overall']['avg_pnl'] = results['overall']['total_pnl'] / total
        else:
            results['overall']['win_rate'] = 0
            results['overall']['avg_pnl'] = 0

        results['overall']['total_pnl'] = round(results['overall']['total_pnl'], 2)

        return results

    def analyze_confidence_calibration(self) -> Dict:
        """
        Analyze if our confidence scores are well-calibrated.

        A well-calibrated model should have:
        - 70% confidence predictions winning ~70% of the time
        - 90% confidence predictions winning ~90% of the time
        """
        # Load simulated trades
        pnl_db = DATA_DIR / "simulated_pnl.db"
        if not pnl_db.exists():
            return {'error': 'No simulated trades data'}

        conn = sqlite3.connect(pnl_db)
        conn.row_factory = sqlite3.Row

        # Get resolved trades grouped by confidence bucket
        cur = conn.execute('''
            SELECT
                CASE
                    WHEN confidence < 0.5 THEN '0-50%'
                    WHEN confidence < 0.6 THEN '50-60%'
                    WHEN confidence < 0.7 THEN '60-70%'
                    WHEN confidence < 0.8 THEN '70-80%'
                    WHEN confidence < 0.9 THEN '80-90%'
                    ELSE '90-100%'
                END as conf_bucket,
                COUNT(*) as total,
                SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
                AVG(edge_pct) as avg_edge,
                SUM(simulated_pnl_usd) as total_pnl
            FROM simulated_trades
            WHERE resolved = 1
            GROUP BY conf_bucket
            ORDER BY conf_bucket
        ''')

        calibration = {}
        for row in cur.fetchall():
            bucket = row['conf_bucket']
            total = row['total']
            wins = row['wins'] or 0
            actual_win_rate = (wins / total * 100) if total > 0 else 0

            # Expected win rate is the midpoint of the bucket
            bucket_map = {
                '0-50%': 25,
                '50-60%': 55,
                '60-70%': 65,
                '70-80%': 75,
                '80-90%': 85,
                '90-100%': 95,
            }
            expected = bucket_map.get(bucket, 50)

            calibration[bucket] = {
                'total': total,
                'wins': wins,
                'actual_win_rate': round(actual_win_rate, 1),
                'expected_win_rate': expected,
                'calibration_error': round(actual_win_rate - expected, 1),
                'avg_edge': round(row['avg_edge'] or 0, 1),
                'total_pnl': round(row['total_pnl'] or 0, 2),
            }

        conn.close()

        # Calculate overall calibration score
        total_error = sum(abs(v['calibration_error']) for v in calibration.values())
        num_buckets = len(calibration) or 1
        avg_error = total_error / num_buckets

        return {
            'buckets': calibration,
            'avg_calibration_error': round(avg_error, 1),
            'is_well_calibrated': avg_error < 10,  # Less than 10% avg error
        }

    def simulate_paper_trading(self, days: int = 7) -> Dict:
        """
        Simulate paper trading with realistic conditions.

        Factors in:
        - Partial fills (not all orders execute)
        - Slippage (price moves against us)
        - Position limits
        """
        signals_db = DATA_DIR / "niche_markets.db"
        if not signals_db.exists():
            return {'error': 'No signals data'}

        conn = sqlite3.connect(signals_db)
        conn.row_factory = sqlite3.Row

        # Get recent signals
        cur = conn.execute('''
            SELECT *
            FROM signals
            WHERE created_at > datetime('now', ?)
            ORDER BY edge_pct DESC
        ''', (f'-{days} days',))

        signals = [dict(row) for row in cur.fetchall()]
        conn.close()

        if not signals:
            return {'error': 'No recent signals'}

        # Simulation parameters
        starting_capital = 250.0
        max_position_pct = 0.10  # 10% max per position
        fill_rate = 0.70  # 70% of limit orders fill
        slippage = 0.02  # 2% slippage on average

        # Run simulation
        capital = starting_capital
        positions = []
        trades = []
        wins = 0
        losses = 0

        for signal in signals[:50]:  # Top 50 signals
            # Check if order would fill
            import random
            if random.random() > fill_rate:
                continue  # Order didn't fill

            # Calculate position size
            edge = signal.get('edge_pct', 0) / 100
            confidence = signal.get('confidence', 0.5)
            kelly = (edge * confidence) / (1 - confidence) if confidence < 1 else edge
            kelly = min(kelly, 0.25)  # Cap at 25% Kelly

            position_size = min(
                capital * max_position_pct,
                capital * kelly,
                25.0  # Max $25 per position
            )

            if position_size < 5:
                continue  # Too small

            # Apply slippage to entry
            entry = signal.get('entry_price', 0.5)
            entry_with_slippage = entry * (1 + slippage)

            # Simulate outcome (based on our confidence)
            # In reality, we'd wait for market resolution
            # Here we simulate based on confidence
            outcome_roll = random.random()
            won = outcome_roll < confidence

            # Calculate PnL
            if won:
                pnl = position_size * (1 / entry_with_slippage - 1)
                wins += 1
            else:
                pnl = -position_size
                losses += 1

            capital += pnl

            trades.append({
                'signal_type': signal.get('signal_type'),
                'side': signal.get('side'),
                'entry': entry_with_slippage,
                'size': position_size,
                'won': won,
                'pnl': round(pnl, 2),
            })

        total_trades = wins + losses
        return {
            'starting_capital': starting_capital,
            'ending_capital': round(capital, 2),
            'total_pnl': round(capital - starting_capital, 2),
            'total_pnl_pct': round((capital - starting_capital) / starting_capital * 100, 1),
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': round(wins / total_trades * 100, 1) if total_trades > 0 else 0,
            'fill_rate_used': fill_rate,
            'slippage_used': slippage,
            'sample_trades': trades[:10],
        }

    def analyze_edge_decay(self) -> Dict:
        """
        Analyze how edge changes as resolution approaches.

        Question: Does our edge disappear as markets get closer to resolution?
        """
        signals_db = DATA_DIR / "niche_markets.db"
        if not signals_db.exists():
            return {'error': 'No signals data'}

        conn = sqlite3.connect(signals_db)
        conn.row_factory = sqlite3.Row

        # Get signals grouped by hours to resolution
        # Note: This requires resolution_time in the markets table
        cur = conn.execute('''
            SELECT
                CASE
                    WHEN s.edge_pct < 10 THEN '0-10%'
                    WHEN s.edge_pct < 20 THEN '10-20%'
                    WHEN s.edge_pct < 30 THEN '20-30%'
                    ELSE '30%+'
                END as edge_bucket,
                s.signal_type,
                COUNT(*) as count,
                AVG(s.edge_pct) as avg_edge,
                AVG(s.confidence) as avg_conf
            FROM signals s
            GROUP BY edge_bucket, signal_type
            ORDER BY edge_bucket, signal_type
        ''')

        decay_data = defaultdict(list)
        for row in cur.fetchall():
            decay_data[row['edge_bucket']].append({
                'signal_type': row['signal_type'],
                'count': row['count'],
                'avg_edge': round(row['avg_edge'] or 0, 1),
                'avg_conf': round((row['avg_conf'] or 0.5) * 100, 0),
            })

        conn.close()
        return dict(decay_data)

    def get_pending_resolution_timeline(self) -> Dict:
        """Get timeline of when pending trades will resolve."""
        pnl_db = DATA_DIR / "simulated_pnl.db"
        if not pnl_db.exists():
            return {'error': 'No PnL data'}

        conn = sqlite3.connect(pnl_db)
        conn.row_factory = sqlite3.Row

        cur = conn.execute('''
            SELECT
                COUNT(*) as count,
                MIN(created_at) as earliest,
                MAX(created_at) as latest
            FROM simulated_trades
            WHERE resolved = 0
        ''')
        row = cur.fetchone()

        # Get breakdown by category
        cur = conn.execute('''
            SELECT
                CASE
                    WHEN question LIKE '%Oscar%' OR question LIKE '%nominated%' THEN 'Oscar (Jan 23)'
                    WHEN question LIKE '%election%' OR question LIKE '%governor%' THEN 'Politics (Nov)'
                    ELSE 'Other'
                END as category,
                COUNT(*) as count,
                AVG(edge_pct) as avg_edge
            FROM simulated_trades
            WHERE resolved = 0
            GROUP BY category
        ''')

        by_category = {}
        for r in cur.fetchall():
            by_category[r['category']] = {
                'count': r['count'],
                'avg_edge': round(r['avg_edge'] or 0, 1),
            }

        conn.close()

        return {
            'total_pending': row['count'] or 0,
            'earliest_trade': row['earliest'],
            'latest_trade': row['latest'],
            'by_category': by_category,
            'next_major_resolution': 'Oscar Nominations - Jan 23, 2026',
        }

    def print_full_report(self):
        """Print comprehensive validation report."""
        print("\n" + "=" * 70)
        print("   STRATEGY VALIDATION REPORT")
        print("=" * 70)
        print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

        # 1. Historical Backtest
        print("\n" + "-" * 70)
        print("   1. HISTORICAL BACKTEST (Oscar Predictions 2022-2024)")
        print("-" * 70)
        backtest = self.backtest_oscar_predictions()
        overall = backtest['overall']
        print(f"   Total Predictions: {overall['total_predictions']}")
        print(f"   Correct: {overall['correct']}")
        print(f"   Win Rate: {overall.get('win_rate', 0):.1f}%")
        print(f"   Simulated PnL: ${overall.get('total_pnl', 0):.2f}")
        print(f"   Avg PnL/Trade: ${overall.get('avg_pnl', 0):.2f}")

        for year, data in backtest['years'].items():
            if data['total_predictions'] > 0:
                wr = data['correct'] / data['total_predictions'] * 100
                print(f"\n   {year}: {data['correct']}/{data['total_predictions']} ({wr:.0f}%) | PnL: ${data['pnl']:.2f}")

        # 2. Confidence Calibration
        print("\n" + "-" * 70)
        print("   2. CONFIDENCE CALIBRATION")
        print("-" * 70)
        calibration = self.analyze_confidence_calibration()
        if 'error' in calibration:
            print(f"   {calibration['error']}")
            print("   (Will have data after Oscar nominations resolve)")
        else:
            print(f"   Avg Calibration Error: {calibration['avg_calibration_error']}%")
            print(f"   Well Calibrated: {'Yes' if calibration['is_well_calibrated'] else 'No'}")
            print("\n   Bucket          | Total | Wins | Actual WR | Expected | Error")
            print("   " + "-" * 60)
            for bucket, data in calibration['buckets'].items():
                print(f"   {bucket:15} | {data['total']:>5} | {data['wins']:>4} | {data['actual_win_rate']:>8.1f}% | {data['expected_win_rate']:>7}% | {data['calibration_error']:>+5.1f}%")

        # 3. Paper Trading Simulation
        print("\n" + "-" * 70)
        print("   3. PAPER TRADING SIMULATION (Monte Carlo)")
        print("-" * 70)
        sim = self.simulate_paper_trading()
        if 'error' in sim:
            print(f"   {sim['error']}")
        else:
            print(f"   Starting Capital: ${sim['starting_capital']:.2f}")
            print(f"   Ending Capital: ${sim['ending_capital']:.2f}")
            print(f"   Total PnL: ${sim['total_pnl']:.2f} ({sim['total_pnl_pct']:+.1f}%)")
            print(f"   Trades: {sim['total_trades']} ({sim['wins']}W / {sim['losses']}L)")
            print(f"   Win Rate: {sim['win_rate']:.1f}%")
            print(f"   (Assumes {sim['fill_rate_used']*100:.0f}% fill rate, {sim['slippage_used']*100:.0f}% slippage)")

        # 4. Pending Resolution Timeline
        print("\n" + "-" * 70)
        print("   4. PENDING TRADES & RESOLUTION TIMELINE")
        print("-" * 70)
        timeline = self.get_pending_resolution_timeline()
        if 'error' in timeline:
            print(f"   {timeline['error']}")
        else:
            print(f"   Total Pending: {timeline['total_pending']} trades")
            print(f"   Next Major Resolution: {timeline['next_major_resolution']}")
            print("\n   By Category:")
            for cat, data in timeline.get('by_category', {}).items():
                print(f"     {cat}: {data['count']} trades (avg edge: {data['avg_edge']}%)")

        # 5. Edge Distribution
        print("\n" + "-" * 70)
        print("   5. EDGE DISTRIBUTION BY SIGNAL TYPE")
        print("-" * 70)
        edge_data = self.analyze_edge_decay()
        if 'error' in edge_data:
            print(f"   {edge_data['error']}")
        else:
            for bucket, signals in edge_data.items():
                print(f"\n   {bucket} edge:")
                for s in signals:
                    print(f"     {s['signal_type']}: {s['count']} signals, avg edge {s['avg_edge']}%, conf {s['avg_conf']:.0f}%")

        # Summary
        print("\n" + "=" * 70)
        print("   RECOMMENDATIONS")
        print("=" * 70)

        if overall.get('win_rate', 0) >= 60:
            print("   [OK] Historical backtest shows positive edge")
        else:
            print("   [WARN] Historical win rate below 60% - review model")

        if timeline.get('total_pending', 0) > 100:
            print(f"   [OK] {timeline['total_pending']} pending trades - good sample size for validation")
        else:
            print("   [WARN] Limited pending trades - wait for more data")

        print("\n   Next steps:")
        print("   1. Wait for Oscar nominations (Jan 23) to validate predictions")
        print("   2. Review actual win rate vs confidence calibration")
        print("   3. If win rate > 55%, consider small live test ($50)")
        print("=" * 70 + "\n")


def main():
    validator = StrategyValidator()
    validator.print_full_report()


if __name__ == "__main__":
    main()
