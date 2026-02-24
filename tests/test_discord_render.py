import pytest

from infrastructure import discord_agent
from src.brain import MarketBrain


def test_discord_embeds_and_solscan_link():
    messy_mint = "Token-Name.v1"
    stats = {
        'net_sol': -0.75,
        'win_rate': 0.4,
        'avg_gain_pct': 0.12,
        'alpha_missed': 0,
        'top_performer': {
            'mint': messy_mint,
            'pct': 0.125,
            'tx_sig': 'ABC123SIGNATURE',
        },
        'count': 3,
    }

    # session embed
    embed = discord_agent.create_session_embed(stats)
    # should include title and a field for Top Performer
    assert getattr(embed, 'title', None) == 'Session Report'
    # Find top performer field
    top_fields = [f for f in getattr(embed, 'fields', []) if f.get('name') == 'Top Performer']
    assert top_fields, f"Top Performer field missing: {getattr(embed, 'fields', None)}"

    # solscan link present in View Transaction field
    vt_fields = [f for f in getattr(embed, 'fields', []) if f.get('name') == 'View Transaction']
    assert vt_fields, 'View Transaction field missing'
    assert 'https://solscan.io/tx/ABC123SIGNATURE' in vt_fields[0].get('value')

    # signal embed
    sig = {'mint': messy_mint, 'vhi_score': 78, 'graduation_action': True, 'tx_sig': 'ABC123SIGNATURE'}
    sembed = discord_agent.create_signal_embed(sig)
    fields = {f['name']: f['value'] for f in getattr(sembed, 'fields', [])}
    assert fields.get('Mint') == messy_mint
    assert 'https://solscan.io/tx/ABC123SIGNATURE' in fields.get('Transaction', '')

    # solscan helper
    mb = MarketBrain(rpc='http://localhost')
    assert mb.get_solscan_url('XYZ') == 'https://solscan.io/tx/XYZ'
