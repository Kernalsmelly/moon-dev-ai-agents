import pandas as pd
import talib
from backtesting import Backtest, Strategy

DATA_PATH = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

class ChikouVolatilityStrategy(Strategy):
    def init(self):
        print("🌙 Moon Dev initiating ChikouVolatility strategy! 🚀")
        self.upper_bb, self.middle_bb, self.lower_bb = self.I(talib.BBANDS, self.data.Close, 20, 2, 2)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14)
        # Create Chikou Span equivalent (lagging line)
        self.chikou_span = self.I(lambda x: x, self.data.Close)
        
    def next(self):
        if len(self.data) < 26:
            return
            
        if self.position:
            # Exit conditions
            if self.position.is_long:
                if self.data.Close[-1] >= self.upper_bb[-1]:
                    print("🌙 Moon Dev long exit at upper BB! Closing position. ✨")
                    self.position.close()
            elif self.position.is_short:
                if self.data.Close[-1] <= self.lower_bb[-1]:
                    print("🌙 Moon Dev short exit at lower BB! Closing position. ✨")
                    self.position.close()
            return

        # Check if we have enough data for Chikou analysis
        if len(self.data) < 26:
            return
            
        # Chikou span analysis (current price vs price 26 periods ago)
        chikou_bullish = self.data.Close[-1] > self.data.Close[-26]
        chikou_bearish = self.data.Close[-1] < self.data.Close[-26]
        
        # Long entry: Chikou bullish + price at lower BB + price bouncing back
        if chikou_bullish and self.data.Close[-1] <= self.lower_bb[-1] and self.data.Close[-1] > self.data.Close[-2]:
            print("🌙 Moon Dev bullish ChikouVolatility setup! Entering long. 🚀")
            entry_price = self.data.Close[-1]
            sl = self.lower_bb[-1] - 0.5 * self.atr[-1]
            tp = self.upper_bb[-1]
            risk_per_unit = entry_price - sl
            if risk_per_unit <= 0:
                return
            risk_amount = self.equity * 0.01
            position_size = risk_amount / risk_per_unit
            position_size = int(round(position_size))
            if position_size > 0:
                self.buy(size=position_size, sl=sl, tp=tp)
                print(f"🌙 Moon Dev entered long with size {position_size}! ✨")

        # Short entry: Chikou bearish + price at upper BB + price bouncing back
        if chikou_bearish and self.data.Close[-1] >= self.upper_bb[-1] and self.data.Close[-1] < self.data.Close[-2]:
            print("🌙 Moon Dev bearish ChikouVolatility setup! Entering short. 🚀")
            entry_price = self.data.Close[-1]
            sl = self.upper_bb[-1] + 0.5 * self.atr[-1]
            tp = self.lower_bb[-1]
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

bt = Backtest(data, ChikouVolatilityStrategy, cash=1000000, margin=1, commission=0.001)
stats = bt.run()
print(stats)
print(stats._strategy) 