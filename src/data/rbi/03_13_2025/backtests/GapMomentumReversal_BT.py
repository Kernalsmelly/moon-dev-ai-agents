# 🌙 Moon Dev Backtest AI Implementation for GapMomentumReversal Strategy 🚀

import pandas as pd
import pandas_ta as ta
from backtesting import Backtest, Strategy

# Data Preparation 🌌
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

# Load and clean data
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# Resample to daily timeframe 🌗
data = data.resample('D').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).dropna()

# Format columns to backtesting.py requirements ✨
data.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

class GapMomentumReversal(Strategy):
    lookback_period = 20  # 20-day lookback for percentiles
    risk_pct = 0.01       # 1% risk per trade
    sl_pct = 0.02         # 2% stop loss
    tp_pct = 0.03         # 3% take profit
    
    def init(self):
        # Calculate daily price range 🌓
        self.daily_range = self.data.High - self.data.Low
        
        # Calculate percentile bands using pandas_ta ✨
        self.top_band = self.I(ta.percentile, self.daily_range, 
                              length=self.lookback_period, q=80)
        self.bottom_band = self.I(ta.percentile, self.daily_range,
                                 length=self.lookback_period, q=20)
        
    def next(self):
        current_range = self.daily_range[-1]
        current_top = self.top_band[-1]
        current_bottom = self.bottom_band[-1]
        
        # Moon Dev debug prints 🌙
        print(f"🌙 Range: {current_range:.2f} | Top: {current_top:.2f} | Bottom: {current_bottom:.2f}")
        
        if not self.position:
            # Long entry condition 🌕
            if current_range >= current_top:
                self.enter_trade('long')
            
            # Short entry condition 🌑
            elif current_range <= current_bottom:
                self.enter_trade('short')
        else:
            # Exit conditions 🚪
            if self.position.is_long and current_range < current_top:
                self.position.close()
                print(f"🌙 Closing LONG at {self.data.Close[-1]:.2f} ✨")
            elif self.position.is_short and current_range > current_bottom:
                self.position.close()
                print(f"🌙 Closing SHORT at {self.data.Close[-1]:.2f} ✨")
    
    def enter_trade(self, direction):
        # Risk management calculations 🔒
        equity = self.equity
        price = self.data.Close[-1]
        
        if direction == 'long':
            sl_price = price * (1 - self.sl_pct)
            tp_price = price * (1 + self.tp_pct)
            risk_per_share = price - sl_price
        else:
            sl_price = price * (1 + self.sl_pct)
            tp_price = price * (1 - self.tp_pct)
            risk_per_share = sl_price - price
            
        if risk_per_share <= 0:
            return
            
        position_size = (equity * self.risk_pct) / risk_per_share
        position_size = int(round(position_size))
        
        if position_size > 0:
            if direction == 'long':
                self.buy(size=position_size, sl=sl_price, tp=tp_price)
                print(f"🚀 MOON DEV LONG ENTRY 🌕 | Size: {position_size} @ {price:.2f}")
            else:
                self.sell(size=position_size, sl=sl_price, tp=tp_price)
                print(f"🚀 MOON DEV SHORT ENTRY 🌑 | Size: {position_size} @ {price:.2f}")

# Run backtest and print full stats 📊
bt = Backtest(data, GapMomentumReversal, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)