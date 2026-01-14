# 🌙✨ Moon Dev Backtest AI Generated Code 🚀
from backtesting import Backtest, Strategy
import pandas as pd
import talib

class RangeBoundPut(Strategy):
    risk_pct = 0.02  # 2% risk per trade
    ma_period = 200
    lookback_period = 20
    
    def init(self):
        # 🌙 Calculate indicators using TA-Lib
        self.sma = self.I(talib.SMA, self.data.Close, self.ma_period)
        
        # Create boolean array for Close > SMA
        self.above_sma = (self.data.Close > self.sma).astype(float)
        
        # Calculate sum of above_sma values over lookback period
        self.sum_above = self.I(talib.SUM, self.above_sma, self.lookback_period)
        
    def next(self):
        # 🌙✨ Core Strategy Logic
        if not self.position:
            # Entry condition: price consistently above SMA for lookback period
            if len(self.data) > self.lookback_period and self.sum_above[-1] >= self.lookback_period:
                entry_price = self.data.Close[-1]
                sl_price = entry_price * 0.8  # 20% stop loss
                tp_price = entry_price * 1.1  # 10% take profit
                
                # 🌙 Risk management calculations
                risk_amount = self.equity * self.risk_pct
                risk_per_share = entry_price - sl_price
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    
                    # 🚀 Execute entry with Moon Dev flair
                    self.buy(size=position_size, sl=sl_price, tp=tp_price)
                    print(f"🌙✨🚀 MOON DEV ENTRY: Long {position_size} units at {entry_price:.2f}")
                    print(f"   🌕 SL: {sl_price:.2f} | 🌟 TP: {tp_price:.2f}")

# 🌙 Data Preparation Ritual
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Cleanse column names with lunar magic ✨
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# Align cosmic columns 🌌
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# Set temporal index 🕰️
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

# 🌙 Launch Backtest Sequence
bt = Backtest(data, RangeBoundPut, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌟 Print Cosmic Performance Report
print("\n🌌🌙✨ MOON DEV FINAL STATS 🌟🚀🌕")
print(stats)
print(stats._strategy)
print("🌙 Backtest Complete - May your equity curve reach lunar heights! 🚀")