# 🌙 Moon Dev Backtest Implementation for MeanReversalAlert Strategy 🚀

import pandas as pd
import talib
from backtesting import Backtest, Strategy

class MeanReversalAlert(Strategy):
    risk_pct = 1  # 1% risk per trade 🌙
    
    def init(self):
        # 🌀 Indicator Calculation with TA-Lib
        self.sma20 = self.I(talib.SMA, self.data.Close, timeperiod=20, name='SMA 20')
        
        # 🎢 Bollinger Bands Calculation
        self.upper_bb = self.I(lambda close: talib.BBANDS(close, 20, 2, 2, 0)[0], 
                              self.data.Close, name='Upper BB')
        self.lower_bb = self.I(lambda close: talib.BBANDS(close, 20, 2, 2, 0)[2], 
                              self.data.Close, name='Lower BB')
        
        # 📏 Volatility Measurement
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 
                         timeperiod=14, name='ATR 14')

    def next(self):
        # 🌌 Moon Dev Core Strategy Logic
        price = self.data.Close[-1]
        
        if not self.position:
            # 🌟 Long Entry: Price breaks below Lower Bollinger Band
            if price < self.lower_bb[-1]:
                atr_value = self.atr[-1]
                entry_price = price
                sl_price = entry_price - 2 * atr_value
                
                # 🧮 Position Sizing Calculation
                risk_per_share = entry_price - sl_price
                if risk_per_share > 0:
                    risk_amount = self.equity * self.risk_pct / 100
                    position_size = int(round(risk_amount / risk_per_share))
                    
                    if position_size > 0:
                        # 🚀 Execute Trade with Moon Dev Flare
                        self.buy(size=position_size, sl=sl_price)
                        print(f"🌕 MOON DEV ALERT: Long @ {entry_price:.2f} | "
                              f"SL: {sl_price:.2f} | Size: {position_size} contracts ✨")

# 📀 Data Preparation Ritual
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# 🧹 Cleanse Column Names
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# 🔮 Proper Column Mapping
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# ⏳ Set Temporal Index
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

# 🌙✨ Launch Backtest Sequence
bt = Backtest(data, MeanReversalAlert, cash=1_000_000, trade_on_close=True)
stats = bt.run()

# 🖨️ Print Full Moon Stats
print("\n🌌🌠 MOON DEV STRATEGY PERFORMANCE REPORT 🌠🌌")
print(stats)
print(stats._strategy)