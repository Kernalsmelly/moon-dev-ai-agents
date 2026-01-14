#!/usr/bin/env python3
"""
Quick Trade Count Test - Verify strategies generate trades without optimization
"""

import pandas as pd
from backtesting import Backtest
import warnings
warnings.filterwarnings('ignore')

# Load data once
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High', 
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

# Import all strategies
from SimpleMomentumCross_BT import SimpleMomentumCross
from RSIMeanReversion_BT import RSIMeanReversion
from VolatilityBreakout_BT import VolatilityBreakout
from BollingerReversion_BT import BollingerReversion
from MACDDivergence_BT import MACDDivergence
from StochasticMomentum_BT import StochasticMomentum
from TrendFollowingMA_BT import TrendFollowingMA
from VolumeWeightedBreakout_BT import VolumeWeightedBreakout
from ATRChannelSystem_BT import ATRChannelSystem
from HybridMomentumReversion_BT import HybridMomentumReversion

strategies = [
    ('SimpleMomentumCross', SimpleMomentumCross),
    ('RSIMeanReversion', RSIMeanReversion),
    ('VolatilityBreakout', VolatilityBreakout),
    ('BollingerReversion', BollingerReversion),
    ('MACDDivergence', MACDDivergence),
    ('StochasticMomentum', StochasticMomentum),
    ('TrendFollowingMA', TrendFollowingMA),
    ('VolumeWeightedBreakout', VolumeWeightedBreakout),
    ('ATRChannelSystem', ATRChannelSystem),
    ('HybridMomentumReversion', HybridMomentumReversion)
]

print("🌙 QUICK TRADE COUNT TEST - DEFAULT PARAMETERS")
print("=" * 80)
print(f"{'Strategy':<30} {'Trades':<10} {'Return %':<12} {'Sharpe':<10} {'Status'}")
print("=" * 80)

results = []
for name, strategy in strategies:
    try:
        bt = Backtest(data, strategy, cash=1000000, commission=0.002)
        stats = bt.run()
        
        trades = stats['# Trades']
        returns = stats['Return [%]']
        sharpe = stats['Sharpe Ratio']
        
        # Check if meets minimum requirements
        status = "✅" if trades >= 25 else "❌"
        sharpe_status = "✅" if sharpe >= 2.0 else "⚠️"
        
        print(f"{name:<30} {trades:<10} {returns:>11.2f}% {sharpe:>9.2f} {status} {sharpe_status}")
        
        results.append({
            'strategy': name,
            'trades': trades,
            'return': returns,
            'sharpe': sharpe
        })
        
    except Exception as e:
        print(f"{name:<30} ERROR: {str(e)[:40]}")

print("=" * 80)

# Summary
passing = sum(1 for r in results if r['trades'] >= 25)
high_sharpe = sum(1 for r in results if r['sharpe'] >= 2.0)

print("\n📊 SUMMARY:")
print(f"   Strategies with 25+ trades: {passing}/{len(strategies)}")
print(f"   Strategies with 2.0+ Sharpe: {high_sharpe}/{len(strategies)}")

if passing >= 8:
    print("\n🏆 SUCCESS! Most strategies generating sufficient trades!")
else:
    print("\n⚠️ Need further parameter adjustment for more trades")

print("\n💡 Note: Run full optimization for best Sharpe ratios")