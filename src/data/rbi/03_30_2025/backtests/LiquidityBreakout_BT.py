# -*- coding: utf-8 -*-
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class LiquidityBreakout(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade
    
    def init(self):
        # Moon Dev Indicator Setup 🌙
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, 14)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14)
        self.swing_high = self.I(talib.MAX, self.data.High, 20)
        self.swing_low = self.I(talib.MIN, self.data.Low, 20)
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 20)
        
        print("🌙✨ MOON DEV STRATEGY INITIALIZED! ✨🚀")

    def next(self):
        # Skip early bars where indicators are not calculated
        if len(self.data) < 20:
            return

        # Moon Dev Strategy Logic 🌙
        current_adx = self.adx[-1]
        current_close = self.data.Close[-1]
        current_volume = self.data.Volume[-1]

        if not self.position:
            # Long Entry Logic 🌙🚀
            if (current_close > self.swing_high[-1] 
                and current_adx > 30 
                and current_volume > self.volume_sma[-1]):
                
                sl = self.swing_low[-1]
                risk_amount = self.risk_per_trade * self.equity
                risk_per_share = current_close - sl
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    tp = current_close + 1.5 * self.atr[-1]
                    
                    if position_size > 0:
                        self.buy(size=position_size, sl=sl, tp=tp)
                        print(f"🌙🚀 BULLISH BREAKOUT! 🚀🌙 Entry: {current_close:.2f}, Size: {position_size}, SL: {sl:.2f}, TP: {tp:.2f}")

            # Short Entry Logic 🌙🔻
            elif (current_close < self.swing_low[-1] 
                  and current_adx > 30 
                  and current_volume > self.volume_sma[-1]):
                
                sl = self.swing_high[-1]
                risk_amount = self.risk_per_trade * self.equity
                risk_per_share = sl - current_close
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    tp = current_close - 1.5 * self.atr[-1]
                    
                    if position_size > 0:
                        self.sell(size=position_size, sl=sl, tp=tp)
                        print(f"🌙🔻 BEARISH BREAKDOWN! 🔻🌙 Entry: {current_close:.2f}, Size: {position_size}, SL: {sl:.2f}, TP: {tp:.2f}")

# Moon Dev Data Preparation 🌙
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}).ffill()

print("🌙✨ DATA LOADED & CLEANED! ✨🚀\n", data.head())

# Moon Dev Backtest Execution 🌙
bt = Backtest(data, LiquidityBreakout, cash=1_000_000, exclusive_orders=True)
stats = bt.run()
print("\n🌙✨ FINAL BACKTEST RESULTS ✨🚀")
print(stats)
print(stats._strategy)