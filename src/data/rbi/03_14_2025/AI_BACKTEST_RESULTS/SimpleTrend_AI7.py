#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🌙 AI7 - SimpleTrend Strategy Backtest
Moon Dev AI Trading Strategy Implementation
Simple but effective trend following to beat buy and hold
"""

import pandas as pd
import pandas_ta as ta
from backtesting import Backtest, Strategy

# Load and preprocess data
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class SimpleTrend(Strategy):
    # Simple but effective parameters
    ma_period = 50
    position_size_pct = 0.98  # Use almost all equity
    
    def init(self):
        # Simple moving average
        def _sma(close, period):
            result = ta.sma(pd.Series(close), length=period)
            if result is not None:
                return result.values
            else:
                return pd.Series(close).rolling(period).mean().values
        
        self.ma = self.I(_sma, self.data.Close, self.ma_period, name=f'SMA{self.ma_period}')
        
        # Price momentum
        def _momentum(close, period=10):
            return (pd.Series(close) / pd.Series(close).shift(period) - 1) * 100
        
        self.momentum = self.I(_momentum, self.data.Close, 10, name='Momentum')

    def next(self):
        # Skip if not enough data
        if len(self.data.Close) < self.ma_period:
            return
        
        price = self.data.Close[-1]
        ma_value = self.ma[-1]
        
        # Simple trend following rules
        bullish = price > ma_value and self.momentum[-1] > 0
        bearish = price < ma_value and self.momentum[-1] < 0
        
        if not self.position:
            # Enter long when price is above MA and has positive momentum
            if bullish:
                position_size = int((self.equity * self.position_size_pct) / price)
                if position_size > 0:
                    self.buy(size=position_size)
                    print(f"🚀 BUY: {price:.2f} | MA: {ma_value:.2f} | Momentum: {self.momentum[-1]:.2f}%")
        
        else:
            # Exit when trend changes
            if self.position.is_long and bearish:
                self.position.close()
                print(f"💰 SELL: {price:.2f} | MA: {ma_value:.2f}")

# 🌙🚀 Execute Backtest
print("🌙✨ Starting SimpleTrend Backtest...")
bt = Backtest(data, SimpleTrend, cash=1_000_000, commission=0.002)
stats = bt.run()

print("\n🌙🎯 BACKTEST RESULTS - SimpleTrend Strategy:")
print("=" * 60)
print(stats)
print(f"\n🌙💎 Buy & Hold Return: {stats['Buy & Hold Return [%]']:.2f}%")
print(f"🌙🚀 Strategy Return: {stats['Return [%]']:.2f}%")
print(f"🌙📊 Number of Trades: {stats['# Trades']}")

if stats['Return [%]'] > stats['Buy & Hold Return [%]'] and stats['# Trades'] > 5:
    print("🌙✅ SUCCESS: Strategy beats buy & hold with sufficient trades!")
    print("DONE")
    print("🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀")
else:
    print("🌙❌ Strategy needs more optimization...")

print("\n🌙📊 Strategy Details:")
print(stats._strategy)
print("=" * 60)