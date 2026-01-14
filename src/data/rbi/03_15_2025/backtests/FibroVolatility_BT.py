# 🌙 Moon Dev's FibroVolatility Backtest 🌙
import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Clean and prepare data 🌌
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')

# Data cleansing ritual 🔮
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class FibroVolatility(Strategy):
    atr_period = 14
    swing_window = 20
    atr_threshold = 100  # 📉 Volatility filter
    risk_pct = 0.01  # 💼 1% risk per trade
    
    def init(self):
        # 🌗 Moon-powered indicators
        self.swing_high = self.I(talib.MAX, self.data.High, timeperiod=self.swing_window)
        self.swing_low = self.I(talib.MIN, self.data.Low, timeperiod=self.swing_window)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, self.atr_period)
        
    def next(self):
        # 🌑 Only trade during GMT+2 daylight (8AM-5PM)
        current_time = self.data.index[-1].tz_localize(None) + pd.Timedelta(hours=2)
        if not (8 <= current_time.hour < 17):
            return
        
        # 🌕 Check for celestial alignments (trade conditions)
        if not self.position and self.atr[-1] < self.atr_threshold:
            swing_high = self.swing_high[-1]
            swing_low = self.swing_low[-1]
            
            if swing_high > swing_low:
                # 🌠 Calculate Fibonacci constellations
                fib_levels = [
                    swing_low + (swing_high - swing_low)*0.382,
                    swing_low + (swing_high - swing_low)*0.5,
                    swing_low + (swing_high - swing_low)*0.618
                ]
                
                current_price = self.data.Close[-1]
                threshold = current_price * 0.005  # 0.5% buffer
                
                for level in fib_levels:
                    if abs(current_price - level) < threshold:
                        # 🌘 Calculate moon rocket fuel (position size)
                        risk_amount = self.equity * self.risk_pct
                        position_size = int(round(risk_amount / self.atr[-1]))
                        
                        if current_price > level:
                            # 🚀 Long entry on support bounce
                            self.buy(
                                size=position_size,
                                sl=current_price - self.atr[-1],
                                tp=current_price + 1.5*self.atr[-1]
                            )
                            print(f"🌕 MOON SHOT! LONG {position_size} @ {current_price:.2f} ✨")
                        else:
                            # 🌑 Short entry on resistance rejection
                            self.sell(
                                size=position_size,
                                sl=current_price + self.atr[-1],
                                tp=current_price - 1.5*self.atr[-1]
                            )
                            print(f"🌒 DARK SIDE ENTERED! SHORT {position_size} @ {current_price:.2f} 🌗")
                        break

# 🌟 Launch backtest sequence
bt = Backtest(data, FibroVolatility, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌠 Print cosmic performance report
print("\n🌌 FINAL MOON MISSION REPORT 🌌")
print(stats)
print(stats._strategy)