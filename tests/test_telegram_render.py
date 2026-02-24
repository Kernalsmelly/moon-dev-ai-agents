import pytest
from datetime import timezone

from infrastructure.telegram_agent import _format_session_message, markdown_escape
from src.brain import MarketBrain


def test_markdownv2_escape_and_solscan_link():
    # Prepare a messy token name and stats payload
    messy_mint = "Token-Name.v1"
    stats = {
        'net_sol': 0.1234,
        'win_rate': 0.5,
        'avg_gain_pct': 0.12,
        'alpha_missed': 0,
        'top_performer': {
            'mint': messy_mint,
            'pct': 0.125,
            'tx_sig': 'ABC123SIGNATURE',
        },
        'count': 1,
    }

    # Raw session message
    raw = _format_session_message('test', stats)
    assert messy_mint in raw

    # Escaped message should contain backslash-escaped dots and dashes
    esc = markdown_escape(raw)
    # Accept either single- or double-backslash escaping (our helper may double-escape backslashes)
    dash_ok = ('\\-' in esc) or ('\\\\-' in esc)
    dot_ok = ('\\.' in esc) or ('\\\\.' in esc)
    assert dash_ok and dot_ok, f"Escaped text didn't contain escaped dash/dot: {esc}"

    # Verify solscan helper returns expected URL
    mb = MarketBrain(rpc='http://localhost')
    url = mb.get_solscan_url('ABC123SIGNATURE')
    assert url == 'https://solscan.io/tx/ABC123SIGNATURE'
