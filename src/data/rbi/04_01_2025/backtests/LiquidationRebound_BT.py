# 🌙 Moon Dev's LiquidationRebound Backtest Script
from backtesting import Backtest, Strategy
import pandas as pd
import pandas_ta as ta
import talib

# 🚀 Data Preparation
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv', parse_dates=['datetime'])
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])

# 🌈 Column Mapping Magic
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

class LiquidationRebound(Strategy):
    oi_period = 5
    oi_drop_threshold = -10  # 10% drop
    atr_period = 14
    swing_window = 20
    risk_pct = 0.01  # 1% of equity
    
    def init(self):
        # 🌟 Indicator Calculations
        self.oi_roc = self.I(talib.ROCP, self.data.open_interest, self.oi_period) * 100
        self.vwap = self.I(ta.vwap, high=self.data.High, low=self.data.Low, close=self.data.Close, volume=self.data.Volume)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, self.atr_period)
        self.swing_low = self.I(talib.MIN, self.data.Low, self.swing_window)
        
        print("✨ Moon Dev Indicators Activated! VWAP, ATR & Swing Lows Ready 🌙")

    def next(self):
        if self.position:
            return  # 🛑 Existing position check
            
        # 🔍 Liquidation Event Detection
        oi_crash = self.oi_roc[-1] <= self.oi_drop_threshold
        price_above_vwap = self.data.Close[-1] > self.vwap[-1]
        
        if oi_crash and price_above_vwap:
            # 📈 Entry Logic
            entry_price = self.data.Close[-1]
            atr_value = self.atr[-1]
            sl_price = self.swing_low[-1]
            
            # 🧮 Risk Management Calculation
            risk_per_share = entry_price - sl_price
            if risk_per_share <= 0:
                print("🌑 Invalid Risk: Moon Dev Shields Activated!")
                return
                
            equity = self._broker.equity
            position_size = int(round((equity * self.risk_pct) / risk_per_share))
            
            if position_size > 0:
                # 🚀 Execute Trade with Bracket Orders
                self.buy(size=position_size,
                        sl=sl_price,
                        tp=entry_price + 2*atr_value,
                        tag="MoonDev_LiquidationRebound")
                
                # 🌕 Debug Prints
                print(f"\n🚀 MOON DEV TRIGGER @ {self.data.index[-1]}")
                print(f"💎 Entry: {entry_price:.2f}")
                print(f"🎯 TP: {entry_price + 2*atr_value:.2f}")
                print(f"🛡️ SL: {sl_price:.2f}")
                print(f"📈 Position Size: {position_size} units")
                print(f"🌊 Current ATR: {atr_value:.2f}")
                print("🌙✨"*10 + "\n")

# 🏁 Run Backtest
bt = Backtest(data, LiquidationRebound, cash=1_000_000, commission=.002)
stats = bt.run()

# 📊 Print Full Statistics
print("\n🌕🌖🌗🌘🌑 MOON DEV FINAL STATS 🌑🌒🌓🌔🌕")
print(stats)
print(stats._strategy)
print("🌙 Strategy Execution Complete! To the Moon! 🚀")