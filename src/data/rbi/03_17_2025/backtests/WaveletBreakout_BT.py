# 🌙 Moon Dev's WaveletBreakout Backtest Implementation 🚀

# Required imports
from backtesting import Backtest, Strategy
from backtesting.lib import crossover, crossunder
import pandas as pd
import talib

# Clean and prepare data
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

class WaveletBreakout(Strategy):
    # Strategy parameters 🌊
    swing_period = 20
    atr_period = 14
    risk_pct = 0.01  # 1% risk per trade
    
    def init(self):
        # Core indicators using TA-Lib ✨
        self.swing_high = self.I(talib.MAX, self.data.High, timeperiod=self.swing_period)
        self.swing_low = self.I(talib.MIN, self.data.Low, timeperiod=self.swing_period)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, self.atr_period)
        
        print("🌙 WaveletBreakout Strategy Activated! ✨")

    def next(self):
        price = self.data.Close[-1]
        prev_high = self.swing_high[-1]
        prev_low = self.swing_low[-1]
        current_atr = self.atr[-1]

        # Moon Dev's Risk Management System 🌙
        equity = self.equity
        position_size = 0

        if not self.position:
            # Short Entry: False breakout above resistance 🌌
            if self.data.High[-1] > prev_high and self.data.Close[-1] < prev_high:
                risk_amount = equity * self.risk_pct
                risk_per_share = prev_high - price
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    tp = price - 2 * (prev_high - price)
                    print(f"🚨 SHORT SIGNAL 🌑 | Price: {price:.2f} | SL: {prev_high:.2f} | TP: {tp:.2f}")
                    self.sell(size=position_size, sl=prev_high, tp=tp)

            # Long Entry: False breakout below support 🌠
            elif self.data.Low[-1] < prev_low and self.data.Close[-1] > prev_low:
                risk_amount = equity * self.risk_pct
                risk_per_share = price - prev_low
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    tp = price + 2 * (price - prev_low)
                    print(f"🚀 LONG SIGNAL 🌕 | Price: {price:.2f} | SL: {prev_low:.2f} | TP: {tp:.2f}")
                    self.buy(size=position_size, sl=prev_low, tp=tp)

        else:
            # Moon Dev's Exit Check 🌙
            if self.position.is_short and crossover(self.data.Close, self.swing_high):
                print(f"🌑 Closing SHORT Position at {price:.2f} | Moon Cycle Complete 🌗")
                self.position.close()
            elif self.position.is_long and crossunder(self.data.Close, self.swing_low):
                print(f"🌕 Closing LONG Position at {price:.2f} | Lunar Profit Achieved 🌓")
                self.position.close()

# Launch Backtest 🚀
bt = Backtest(data, WaveletBreakout, cash=1_000_000, exclusive_orders=True)
stats = bt.run()

# Print Full Moon Report 🌕
print("\n" + "="*50)
print("🌙 FINAL MOON DEV STRATEGY REPORT 🌙")
print("="*50 + "\n")
print(stats)
print("\n" + "="*50)
print("🌙 STRATEGY DETAIL ANALYSIS 🌙")
print("="*50 + "\n")
print(stats._strategy)