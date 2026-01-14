import pandas as pd
import talib
from backtesting import Backtest, Strategy

DATA_PATH = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

class VolatilityExtremeStrategy(Strategy):
    def init(self):
        print("🌙 Moon Dev initiating VolatilityExtreme strategy! 🚀")
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14)
        self.atr_mean = self.I(talib.SMA, self.atr, 50)
        self.atr_std = self.I(talib.STDDEV, self.atr, 50)
        self.z_score = self.I(lambda a, m, s: (a - m) / s if s != 0 else 0, self.atr, self.atr_mean, self.atr_std)

    def next(self):
        if self.position:
            if abs(self.z_score[-1]) < 1:
                print("🌙 Moon Dev mean reversion complete! Closing position. ✨")
                self.position.close()
            return

        if self.z_score[-1] < -3:
            print("🌙 Moon Dev extreme low volatility! Entering long. 🚀")
            entry_price = self.data.Close[-1]
            sl = entry_price - 2 * self.atr[-1]
            risk_per_unit = entry_price - sl
            if risk_per_unit <= 0:
                return
            risk_amount = self.equity * 0.01
            position_size = risk_amount / risk_per_unit
            position_size = int(round(position_size))
            tp = entry_price + 2 * risk_per_unit
            if position_size > 0:
                self.buy(size=position_size, sl=sl, tp=tp)
                print(f"🌙 Moon Dev entered long with size {position_size}! ✨")

        if self.z_score[-1] > 2:
            print("🌙 Moon Dev extreme high volatility! Entering short. 🚀")
            entry_price = self.data.Close[-1]
            sl = entry_price + 2 * self.atr[-1]
            risk_per_unit = sl - entry_price
            if risk_per_unit <= 0:
                return
            risk_amount = self.equity * 0.01
            position_size = risk_amount / risk_per_unit
            position_size = int(round(position_size))
            tp = entry_price - 2 * risk_per_unit
            if position_size > 0:
                self.sell(size=position_size, sl=sl, tp=tp)
                print(f"🌙 Moon Dev entered short with size {position_size}! ✨")

data = pd.read_csv(DATA_PATH, parse_dates=['datetime'], index_col='datetime')
print("🌙 Moon Dev loading BTC-USD data... 🚀")
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
print("🌙 Moon Dev data cleaning complete! ✨")

bt = Backtest(data, VolatilityExtremeStrategy, cash=1000000, margin=1, commission=0.001)
stats = bt.run()
print(stats)
print(stats._strategy) 