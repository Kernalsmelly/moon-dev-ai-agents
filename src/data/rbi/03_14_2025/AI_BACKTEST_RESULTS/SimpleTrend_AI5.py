# 🌙 Moon Dev's Simple Trend Strategy - Beat Buy and Hold
import pandas as pd
import numpy as np
from backtesting import Backtest, Strategy

# Data Preparation
print("🌙 Loading BTC-USD 15m data for SimpleTrend strategy...")
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

print(f"🚀 Data loaded: {len(data)} bars from {data.index[0]} to {data.index[-1]}")

def sma(series, period):
    if isinstance(series, np.ndarray):
        series = pd.Series(series)
    return series.rolling(period).mean().values

class SimpleTrend(Strategy):
    
    def init(self):
        # Simple moving averages
        self.sma20 = self.I(sma, self.data.Close, 20)
        self.sma50 = self.I(sma, self.data.Close, 50)
        
        print("🌙✨ SimpleTrend Strategy Initialized!")

    def next(self):
        if len(self.sma50) < 50:
            return
            
        current_close = self.data.Close[-1]
        sma20_val = self.sma20[-1]
        sma50_val = self.sma50[-1]
        
        # Check for valid values
        if np.isnan(sma20_val) or np.isnan(sma50_val):
            return

        if not self.position:
            # Buy when SMA20 crosses above SMA50 (golden cross)
            if sma20_val > sma50_val and self.sma20[-2] <= self.sma50[-2]:
                # Use full portfolio for maximum leverage
                cash_available = self.equity * 0.95  # Use 95% of equity
                position_size = int(cash_available / current_close)
                if position_size > 0:
                    self.buy(size=position_size)
                    print(f"🚀🌙 GOLDEN CROSS BUY! Entry: {current_close:.2f}, Size: {position_size}")
        
        else:
            # Sell when SMA20 crosses below SMA50 (death cross)
            if sma20_val < sma50_val and self.sma20[-2] >= self.sma50[-2]:
                print(f"🌕 DEATH CROSS SELL at {current_close:.2f}")
                self.position.close()

# Launch Backtest
print("🚀 Launching SimpleTrend backtest with $1,000,000 portfolio...")
bt = Backtest(data, SimpleTrend, cash=1_000_000, commission=0.002)
stats = bt.run()

print("\n" + "="*60)
print("🌙 MOON DEV SIMPLE TREND STRATEGY RESULTS 🌙")
print("="*60)
print(stats)
print("\n🎯 Buy and Hold Benchmark: 127.77% return ($2,277,687)")
print(f"🚀 Strategy Return: {stats['Return [%]']:.2f}%")
print(f"💰 Strategy Final Value: ${stats['Equity Final [$]']:,.2f}")
print(f"📊 Total Trades: {stats['# Trades']}")

if stats['Return [%]'] > 127.77 and stats['# Trades'] > 5:
    print("🏆 SUCCESS: Strategy beats buy and hold with sufficient trades!")
else:
    print("❌ Strategy needs improvement...")
    
print("="*60)