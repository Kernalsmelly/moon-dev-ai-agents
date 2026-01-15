import os
import json
import tempfile

import pytest

from src.brain import MarketBrain


def test_reload_with_corrupted_json(tmp_path, monkeypatch):
    # create a valid profiles file
    p = tmp_path / 'profiles.json'
    p.write_text(json.dumps({"A": 1.5}))

    monkeypatch.setenv('WHALE_PROFILES_PATH', str(p))
    b = MarketBrain(rpc=None, whales=[])
    # ensure initial load succeeded
    assert b.whale_profiles.get('A') == 1.5

    # corrupt the file
    p.write_text('{ this is : not valid json }')

    # reload should return False and preserve previous profile
    ok = b.reload_whale_profiles()
    assert ok is False
    assert b.whale_profiles.get('A') == 1.5


def test_performance_bucket_assignment(tmp_path, monkeypatch):
    # create a small alpha_journal.csv mock and test analyzer bucketing
    csv = tmp_path / 'alpha_journal.csv'
    # create a row with alpha_score = 15.0, expected_out_sol=0.2, input_amount_sol=0.1 (win)
    csv.write_text('ts,mint,name,volume_pct,expected_out_raw,expected_out_sol,input_amount_sol,unitsConsumed,balance_lamports,success,alpha_score,whale_multiplier\n')
    with open(csv, 'a', encoding='utf-8') as fh:
        fh.write('2026-01-14T00:00:00Z,FAKE,FAKE,500,0,0.2,0.1,100,,True,15.0,1.2\n')

    # import analyzer functions locally to run bucketing
    from src.analyzer import load_data, normalize_expected_out, compute_metrics, performance_by_alpha_score

    df = load_data(csv)
    df, _ = normalize_expected_out(df)
    metrics = compute_metrics(df)
    buckets = performance_by_alpha_score(metrics['df'])

    # find 10-25 bucket
    bucket_map = {b[0]: b for b in buckets}
    assert '10-25' in bucket_map
    label, count, win_rate, avg_pl = bucket_map['10-25']
    assert count == 1
    assert pytest.approx(win_rate, rel=1e-6) == 100.0
    assert pytest.approx(avg_pl, rel=1e-6) == 0.1
