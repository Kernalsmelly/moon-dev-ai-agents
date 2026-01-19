import os
import csv
import pytest



def test_telemetry_unprofitable_entry(tmp_path, monkeypatch):
    # Ensure MarketBrain writes execution events to the test path
    ev = tmp_path / 'execution_events.csv'
    # ensure the file does not exist so MarketBrain writes the header
    if ev.exists():
        ev.unlink()
    monkeypatch.setenv('EXECUTION_LOG_PATH', str(ev))
    # ensure config module picks up the test path
    try:
        import src.config as cfg
        setattr(cfg, 'EXECUTION_LOG_PATH', str(ev))
    except Exception:
        pass

    # import MarketBrain after we've monkeypatched env and config so
    # src.config.EXECUTION_LOG_PATH is honored during the write
    from src.brain import MarketBrain
    brain = MarketBrain(rpc='https://example.devnet')

    # Simulate an aborted_unprofitable telemetry event
    payload = {
        'batch_chunk_indices': [1],
        'profit_info': {'net_profit_sol': -0.00012},
        'delta_to_break_even_sol': -0.00012,
        'note': 'unit_test_simulated_abort',
    }
    brain._log_execution_event('TESTMINT', 'aborted_unprofitable', payload)

    # Read back the CSV and assert event present with delta in the JSON payload
    rows = []
    with open(str(ev), 'r', encoding='utf-8') as fh:
        r = csv.DictReader(fh)
        for row in r:
            rows.append(row)

    assert any(r.get('event_type') == 'aborted_unprofitable' for r in rows)
    found = next((r for r in rows if r.get('event_type') == 'aborted_unprofitable'), None)
    assert found is not None
    # parse the JSON payload column
    import json as _json
    payload = _json.loads(found.get('data_json') or '{}')
    assert 'delta_to_break_even_sol' in payload
    assert isinstance(payload['delta_to_break_even_sol'], (int, float)) or (isinstance(payload['delta_to_break_even_sol'], str) and payload['delta_to_break_even_sol'] != '')
