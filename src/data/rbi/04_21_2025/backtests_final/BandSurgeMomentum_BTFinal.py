from backtesting import Backtest, Strategy
import pandas as pd
import talib

class BandSurgeMomentum(Strategy):
    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        
        # Calculate indicators with TA-Lib
        self.bb_upper, self.bb_middle, self.bb_lower = self.I(talib.BBANDS, close, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
        self.adx = self.I(talib.ADX, high, low, close, timeperiod=14)
        self.rsi = self.I(talib.RSI, close, timeperiod=2)
        self.atr = self.I(talib.ATR, high, low, close, timeperiod=14)
        
        print("🌙✨ Moon Dev Indicators Activated! Lunar engines primed! 🚀")

    def next(self):
        if len(self.data) < 40:
            return
        
        prev_idx = -2
        current_idx = -1
        
        # Entry logic
        if not self.position:
            adx_rising = self.adx[prev_idx] > self.adx[prev_idx-1]
            adx_strong = self.adx[prev_idx] > 25
            close_above_upper = self.data.Close[prev_idx] > self.bb_upper[prev_idx]
            
            if close_above_upper and adx_rising and adx_strong:
                risk_amount = self.equity * 0.01
                atr = self.atr[current_idx]
                position_size = int(round(risk_amount / atr))
                
                if position_size > 0:
                    print(f"🌙🚀 MOON DEV ENTRY 🚀🌙 | BB Breakout Confirmed! | Size: {position_size}")
                    self.buy(size=position_size)
        
        # Exit logic
        if self.position:
            if self.rsi[prev_idx] < 70 and self.rsi[current_idx] >= 70:
                print(f"✨🛑 MOON DEV EXIT 🛑✨ | RSI Cool Down! | RSI: {self.rsi[current_idx]:.1f}")
                self.position.close()
            
            if self.data.Low[current_idx] < self.bb_middle[current_idx]:
                print(f"🌙💥 STOP TRIGGERED 💥🌙 | Middle BB Defense! | Price: {self.data.Low[current_idx]:.2f}")
                self.position.close()

# Data preparation
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})
data['datetime'] = pd.to_datetime(data['datetime'])
data = data.set_index('datetime').sort_index()

# Launch backtest
bt = Backtest(data, BandSurgeMomentum, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)