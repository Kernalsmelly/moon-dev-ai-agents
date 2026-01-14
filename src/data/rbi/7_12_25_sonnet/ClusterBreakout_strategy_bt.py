import pandas as pd
import talib
from backtesting import Backtest, Strategy

DATA_PATH = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

class ClusterBreakoutStrategy(Strategy):
    def init(self):
        print("🌙 Moon Dev initiating ClusterBreakout strategy! 🚀")
        self.sma_50 = self.I(talib.SMA, self.data.Close, 50)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14)
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 20)
        # Using price levels as volume cluster proxies
        self.high_cluster = self.I(talib.MAX, self.data.High, 20)
        self.low_cluster = self.I(talib.MIN, self.data.Low, 20)
        
    def next(self):
        if self.position:
            # Exit on ATR-based targets or trailing stop
            if self.position.is_long:
                if self.data.Close[-1] <= self.sma_50[-1]:
                    print("🌙 Moon Dev long trend break! Closing position. ✨")
                    self.position.close()
            elif self.position.is_short:
                if self.data.Close[-1] >= self.sma_50[-1]:
                    print("🌙 Moon Dev short trend break! Closing position. ✨")
                    self.position.close()
            return

        # Volume spike confirmation
        volume_spike = self.data.Volume[-1] > 1.5 * self.volume_sma[-1]
        
        # Long entry: breakout above high cluster with volume
        breakout_above = self.data.Close[-1] > self.high_cluster[-1] and self.data.Close[-1] > self.sma_50[-1]
        
        if breakout_above and volume_spike:
            print("🌙 Moon Dev bullish ClusterBreakout detected! Entering long. 🚀")
            entry_price = self.data.Close[-1]
            sl = self.high_cluster[-1] - 0.5 * self.atr[-1]
            tp = entry_price + 2 * self.atr[-1]
            risk_per_unit = entry_price - sl
            if risk_per_unit <= 0:
                return
            risk_amount = self.equity * 0.01
            position_size = risk_amount / risk_per_unit
            position_size = int(round(position_size))
            if position_size > 0:
                self.buy(size=position_size, sl=sl, tp=tp)
                print(f"🌙 Moon Dev entered long with size {position_size}! ✨")

        # Short entry: breakout below low cluster with volume
        breakout_below = self.data.Close[-1] < self.low_cluster[-1] and self.data.Close[-1] < self.sma_50[-1]
        
        if breakout_below and volume_spike:
            print("🌙 Moon Dev bearish ClusterBreakout detected! Entering short. 🚀")
            entry_price = self.data.Close[-1]
            sl = self.low_cluster[-1] + 0.5 * self.atr[-1]
            tp = entry_price - 2 * self.atr[-1]
            risk_per_unit = sl - entry_price
            if risk_per_unit <= 0:
                return
            risk_amount = self.equity * 0.01
            position_size = risk_amount / risk_per_unit
            position_size = int(round(position_size))
            if position_size > 0:
                self.sell(size=position_size, sl=sl, tp=tp)
                print(f"🌙 Moon Dev entered short with size {position_size}! ✨")

data = pd.read_csv(DATA_PATH, parse_dates=['datetime'], index_col='datetime')
print("🌙 Moon Dev loading BTC-USD data... 🚀")
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
print("🌙 Moon Dev data cleaning complete! ✨")

bt = Backtest(data, ClusterBreakoutStrategy, cash=1000000, margin=1, commission=0.001)
stats = bt.run()
print(stats)
print(stats._strategy) 