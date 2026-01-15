#!/usr/bin/env python3
"""Simple performance summary for shadow-mode runs.

Reads data/execution_events.csv and prints:
- Total Simulated Exits
- Average Compute Units (unitsConsumed)
- Average Price Impact (estimated_impact_pct)

This is a lightweight script for quick baseline reporting.
"""
import argparse
import csv
import json
import os
from statistics import mean
import src.config as config


def summarize(path=None):
    # Default to config value if not provided
    if path is None:
        path = getattr(config, 'EXECUTION_LOG_PATH', None)
        if path is None:
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'execution_events.csv')
    if not os.path.exists(path):
        print(f"No execution events found at {path}")
        return

    units = []
    impacts = []
    simulated_exits = 0

    # For volatility correlation we examine chunk_executed entries and their
    # 'estimated_impact_pct' and 'attempts' fields to see if higher impact leads
    # to more attempts/retries.
    rows = []
    with open(path, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            et = r.get('event_type')
            try:
                d = json.loads(r.get('data_json') or '{}')
            except Exception:
                d = {}
            if et in ('exit_simulated', 'chunk_executed'):
                # count simulated exits for exit_simulated only
                if et == 'exit_simulated':
                    simulated_exits += 1
                uc = d.get('unitsConsumed')
                if uc is not None:
                    try:
                        units.append(float(uc))
                    except Exception:
                        pass
                imp = d.get('estimated_impact_pct')
                if imp is not None:
                    try:
                        impacts.append(float(imp))
                    except Exception:
                        pass
            # collect rows for correlation analysis
            if et == 'chunk_executed':
                try:
                    rows.append({
                        'impact': float(d.get('estimated_impact_pct') or 0),
                        'attempts': int(d.get('attempts') or 0),
                        'quote_latency_ms': float(d.get('quote_latency_ms') or 0),
                        'birdeye_latency_ms': float(d.get('birdeye_latency_ms') or 0),
                    })
                except Exception:
                    pass

    avg_units = mean(units) if units else 0
    avg_impact = mean(impacts) if impacts else 0
    # compute average quote and birdeye latencies when available (from chunk_executed rows)
    quote_lats = [r.get('quote_latency_ms') for r in rows if isinstance(r.get('quote_latency_ms'), (int, float))]
    birdeye_lats = [r.get('birdeye_latency_ms') for r in rows if isinstance(r.get('birdeye_latency_ms'), (int, float))]
    avg_quote_lat = mean(quote_lats) if quote_lats else None
    avg_birdeye_lat = mean(birdeye_lats) if birdeye_lats else None

    print(f"Total Simulated Exits: {simulated_exits}")
    print(f"Average Compute Units: {avg_units:.1f}")
    print(f"Average Price Impact: {avg_impact:.3f}%")
    print(f"Average Quote Latency (ms): {avg_quote_lat if avg_quote_lat is not None else 'n/a'}")
    print(f"Average Birdeye Latency (ms): {avg_birdeye_lat if avg_birdeye_lat is not None else 'n/a'}")

    # Volatility correlation: compute simple correlation between impact and attempts
    if rows:
        impacts_list = [r['impact'] for r in rows]
        attempts_list = [r['attempts'] for r in rows]
        # compute Pearson correlation coefficient (best-effort)
        try:
            import math
            n = len(rows)
            mean_imp = mean(impacts_list)
            mean_att = mean(attempts_list)
            num = sum((impacts_list[i] - mean_imp) * (attempts_list[i] - mean_att) for i in range(n))
            den = math.sqrt(sum((impacts_list[i] - mean_imp) ** 2 for i in range(n)) * sum((attempts_list[i] - mean_att) ** 2 for i in range(n)))
            corr = num / den if den != 0 else 0.0
        except Exception:
            corr = 0.0
        print(f"Volatility Correlation (impact vs attempts): {corr:.3f}")
        # additionally compute correlation between impact and birdeye latency
        try:
            try:
                # align birdeye latencies with impacts_list via rows ordering
                if birdeye_lats and len(birdeye_lats) == len(impacts_list):
                    import math
                    n2 = len(impacts_list)
                    mean_imp2 = mean(impacts_list)
                    mean_b = mean(birdeye_lats)
                    num2 = sum((impacts_list[i] - mean_imp2) * (birdeye_lats[i] - mean_b) for i in range(n2))
                    den2 = math.sqrt(sum((impacts_list[i] - mean_imp2) ** 2 for i in range(n2)) * sum((birdeye_lats[i] - mean_b) ** 2 for i in range(n2)))
                    corr2 = num2 / den2 if den2 != 0 else 0.0
                    print(f"Volatility Correlation (impact vs birdeye_latency): {corr2:.3f}")
            except Exception:
                pass
        except Exception:
            pass
    else:
        print("Volatility Correlation (impact vs attempts): no data")

    # compute success rate from chunking_completed events when available
    success_n = 0
    success_d = 0
    try:
        with open(path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                if r.get('event_type') == 'chunking_completed':
                    try:
                        d = json.loads(r.get('data_json') or '{}')
                        s = int(d.get('succeeded_chunks') or 0)
                        t = int(d.get('total_chunks') or 0)
                        success_n += s
                        success_d += t
                    except Exception:
                        continue
    except Exception:
        # non-fatal: file may be locked or unreadable
        pass

    success_rate = (success_n / success_d) if success_d else None
    print(f"Success Rate (chunks filled): {success_rate if success_rate is not None else 'n/a'}")

    # Health grade (A-F) based on latencies and success rate
    def grade(avg_q, avg_b, sr):
        # default to worst grade if missing
        if avg_q is None or avg_b is None or sr is None:
            return 'C'
        if avg_q < 100 and avg_b < 100 and sr >= 0.9:
            return 'A'
        if avg_q < 200 and avg_b < 200 and sr >= 0.8:
            return 'B'
        if avg_q < 500 and avg_b < 500 and sr >= 0.6:
            return 'C'
        if avg_q < 1000 and avg_b < 1000 and sr >= 0.4:
            return 'D'
        return 'F'

    health = grade(avg_quote_lat, avg_birdeye_lat, success_rate)
    print(f"Health Grade: {health}")

    # If requested, return structured summary for optional reporting
    summary = {
        'total_simulated_exits': simulated_exits,
        'average_compute_units': avg_units,
        'average_price_impact_pct': avg_impact,
        'average_quote_latency_ms': avg_quote_lat,
        'average_birdeye_latency_ms': avg_birdeye_lat,
        'volatility_correlation': (corr if 'corr' in locals() else None),
        'impact_vs_birdeye_corr': (corr2 if 'corr2' in locals() else None),
        'success_rate': success_rate,
        'health_grade': health,
    }
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', '-p', help='Path to execution events CSV (overrides config)', default=None)
    parser.add_argument('--report', action='store_true', help='Write a JSON report to data/reports/shadow_report_[TIMESTAMP].json')
    args = parser.parse_args()
    summary = summarize(path=args.path)
    if args.report and summary is not None:
        # Determine reports directory based on execution CSV path
        report_target = args.path or getattr(config, 'EXECUTION_LOG_PATH', None)
        if report_target is None:
            report_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'reports')
        else:
            if not os.path.isabs(report_target):
                base = os.path.join(os.path.dirname(os.path.dirname(__file__)), os.path.dirname(report_target))
            else:
                base = os.path.dirname(report_target)
            report_dir = os.path.join(base, 'reports')
        os.makedirs(report_dir, exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime('%Y%m%dT%H%M%S')
        out_path = os.path.join(report_dir, f'shadow_report_{ts}.json')
        try:
            with open(out_path, 'w', encoding='utf-8') as fh:
                json.dump(summary, fh, indent=2)
            print(f"Saved report to {out_path}")
        except Exception as e:
            print(f"Failed to write report: {e}")
