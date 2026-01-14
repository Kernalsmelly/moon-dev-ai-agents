# 🌙 Moon Dev's SymmetricVolatility Backtest AI Implementation 🚀
import pandas as pd
import talib
from backtesting import Strategy

# Clean and prepare data 🌌
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class SymmetricVolatility(Strategy):
    risk_percent = 0.01  # 1% risk per trade 🌗
    
    def init(self):
        # 🌟 Cosmic Indicators
        self.swing_high = self.I(talib.MAX, self.data.High, 5, name='Swing High')
        self.swing_low = self.I(talib.MIN, self.data.Low, 5, name='Swing Low')
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14, name='ATR')
        self.atr_ma = self.I(talib.SMA, self.atr, 14, name='ATR MA')
        
        # 🌠 Celestial Patterns
        self.bullish_engulf = self.I(talib.CDLENGULFING, self.data.Open, self.data.High, 
                                   self.data.Low, self.data.Close, name='Bullish Engulf')
        self.bearish_engulf = self.I(lambda O,H,L,C: talib.CDLENGULFING(O,H,L,C) * -1,
                                   self.data.Open, self.data.High, self.data.Low, self.data.Close,
                                   name='Bearish Engulf')
        
        self.last_swing_high = None
        self.last_swing_low = None
        self.swing_diff = 0

    def next(self):
        # 🌑 Track Lunar Cycles (Swing Points)
        if self.data.High[-1] == self.swing_high[-1]:
            self.last_swing_high = self.data.High[-1]
            print(f"🚀 NEW COSMIC HIGH: {self.last_swing_high:.2f}")
            
        if self.data.Low[-1] == self.swing_low[-1]:
            self.last_swing_low = self.data.Low[-1]
            print(f"🌊 NEW ABYSSAL LOW: {self.last_swing_low:.2f}")

        if not (self.last_swing_high and self.last_swing_low):
            return

        # 🌓 Calculate Fibonacci Event Horizon
        self.swing_diff = self.last_swing_high - self.last_swing_low
        fib_levels = {
            '382': self.last_swing_high - self.swing_diff * 0.382,
            '500': self.last_swing_high - self.swing_diff * 0.5,
            '618': self.last_swing_high - self.swing_diff * 0.618
        }

        current_close = self.data.Close[-1]
        atr_spike = self.atr[-1] > self.atr_ma[-1] * 1.2

        # 🌕 Long Entry Constellation
        if not self.position:
            for level, price in fib_levels.items():
                if abs(current_close - price) <= self.atr[-1] * 0.5:
                    if self.bullish_engulf[-1] > 0:
                        # 🌙 Calculate Position Size
                        risk_amount = self.equity * self.risk_percent
                        sl = self.last_swing_low
                        risk_per_share = current_close - sl
                        if risk_per_share <= 0: continue
                        
                        size = int(round(risk_amount / risk_per_share))
                        tp = current_close + self.swing_diff * 1.272
                        
                        self.buy(size=size, sl=sl, tp=tp)
                        print(f"🌙✨ LONG ENTRY! Size: {size} | SL: {sl:.2f} | TP: {tp:.2f}")
                        break