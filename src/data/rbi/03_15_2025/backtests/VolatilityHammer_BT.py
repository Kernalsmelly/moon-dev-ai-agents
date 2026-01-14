# 🌙 Moon Dev's VolatilityHammer Backtest Implementation 🚀

import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Clean and prepare data 🌈
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean column names 🧹
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# Proper column mapping 📊
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# Convert and set index ⏳
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class VolatilityHammer(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade 🛡️
    
    def init(self):
        # Core indicators 📈
        self.sma20 = self.I(talib.SMA, self.data.Close, 20, name='SMA20')
        self.hammer = self.I(talib.CDLHAMMER, self.data.Open, self.data.High, 
                           self.data.Low, self.data.Close, name='HAMMER')
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, 
                        self.data.Close, 14, name='ATR')
        self.atr_sma = self.I(talib.SMA, self.atr, 20, name='ATR_SMA')
        
    def next(self):
        # Wait for sufficient data ⏳
        if len(self.data) < 20:
            return
            
        # Current market conditions 🌐
        price = self.data.Close[-1]
        hammer_detected = self.hammer[-1] == 100
        in_downtrend = price < self.sma20[-1]
        volatile_market = self.atr[-1] > self.atr_sma[-1]
        
        # Moon Dev Debug Console 📢
        if hammer_detected:
            print(f"🌙✨ HAMMER DETECTED! | Price: {price:.2f} | ATR: {self.atr[-1]:.2f} | Volatile: {volatile_market}")
            
        # Entry Logic 🚪
        if not self.position and hammer_detected and in_downtrend and volatile_market:
            sl = self.data.Low[-1]  # Hammer's low as stop loss 🛑
            risk_per_unit = self.data.Close[-1] - sl
            if risk_per_unit <= 0:
                return  # Skip invalid risk
            
            # Position sizing calculation 🧮
            equity_risk = self.equity * self.risk_per_trade
            position_size = int(round(equity_risk / risk_per_unit))
            position_size = max(position_size, 1)  # Minimum 1 unit
            
            # Take profit calculation 🎯
            tp = self.data.Close[-1] + 1.5 * self.atr[-1]
            
            # Execute trade ⚡
            self.buy(size=position_size,
                    sl=sl,
                    tp=tp,
                    tag="🌕 VOLATILITY HAMMER STRIKE!")
            print(f"🚀 ENTRY | Size: {position_size} | Risk: {risk_per_unit:.2f} | TP: {tp:.2f}")

# Run backtest with Moon Dev settings 🌙
bt = Backtest(data, VolatilityHammer, 
             cash=1_000_000,
             commission=.002,
             exclusive_orders=True)

stats = bt.run()
print("\n" + "="*50)
print("🌕 MOON DEV FINAL BACKTEST STATS 🌕")
print("="*50 + "\n")
print(stats)
print("\n" + "="*50)
print("STRATEGY METRICS 💹")
print("="*50)
print(stats._strategy)