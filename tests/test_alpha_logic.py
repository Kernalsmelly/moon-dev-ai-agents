import math

import pytest

from src.brain import MarketBrain


def make_brain():
    b = MarketBrain(rpc=None, whales=[])
    # avoid loading disk profiles during unit tests
    b.whale_profiles = {}
    return b


def test_alpha_score_vol_zero():
    b = make_brain()
    score = b._calculate_alpha_score('any', trade_sol=1.0, token_volume=0)
    assert score == 0.0


def test_alpha_score_small_volume_floor():
    b = make_brain()
    # vol=500 -> vol/1000=0.5 -> log10(0.5) negative -> floored to 0
    score = b._calculate_alpha_score('any', trade_sol=2.0, token_volume=500)
    assert score == 0.0


def test_alpha_score_large_volume_multipliers():
    b = make_brain()
    # set explicit multiplier
    b.whale_profiles['W'] = 1.0
    # vol=1_000_000 => vol/1000=1000 -> log10=3
    score = b._calculate_alpha_score('W', trade_sol=2.0, token_volume=1_000_000)
    assert pytest.approx(score, rel=1e-9) == 2.0 * 1.0 * 3.0

    b.whale_profiles['W'] = 1.5
    score2 = b._calculate_alpha_score('W', trade_sol=2.0, token_volume=1_000_000)
    assert pytest.approx(score2, rel=1e-9) == 2.0 * 1.5 * 3.0
