import talib
import pandas as pd
from backtesting import Backtest, Strategy

class VoltaicBreakout(Strategy):
    n1 = 20
    n2 = 2
    
    def init(self):
        self.keltner_middle = self.I(talib.EMA, self.data.Close, timeperiod=self.n1)
        self.keltner_upper = self.keltner_middle + 2 * self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=self.n1)
        self.keltner_lower = self.keltner_middle - 2 * self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=self.n1)
        
        self.bb_middle = self.I(talib.SMA, self.data.Close, timeperiod=self.n1)
        self.bb_std = self.I(talib.STDDEV, self.data.Close, timeperiod=self.n1)
        self.bb_upper = self.bb_middle + self.n2 * self.bb_std
        self.bb_lower = self.bb_middle - self.n2 * self.bb_std
        
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.rsi_previous = self.rsi.shift(1)
        
        self.low_previous = self.data.Low.shift(1)
        
    def next(self):
        if not self.position:
            if self.data.Low[-1] < self.low_previous[-1] and self.rsi[-1] > self.rsi_previous[-1]:
                if (self.data.Close[-2] < self.keltner_upper[-2] and self.data.Close[-1] > self.keltner_upper[-1]):
                    if self.bb_upper[-1] > self.bb_upper[-2]:
                        risk_amount = self.equity * 0.01
                        risk = self.data.Close[-1] - self.keltner_lower[-1]
                        position_size = risk_amount / risk
                        self.buy(size=min(1.0, position_size))  # Ensure size is fraction of equity
                        print(f"🌙 VoltaicBreakout Entry Signal! Bought {min(1.0, position_size):.2%} of equity. 🚀")
        else:
            if (self.keltner_middle[-2] < self.data.Close[-2] and self.keltner_middle[-1] > self.data.Close[-1]):
                self.position.close()
                print("✨ VoltaicBreakout Exit Signal! Closed position. 💰")
            
            if self.bb_upper[-1] < self.bb_upper[-2]:
                self.position.close()
                print("🚨 Emergency Exit! Volatility collapsed. Position closed. 🛡️")

# Load data
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.columns = [col.capitalize() for col in data.columns]
data['Datetime'] = pd.to_datetime(data['datetime'])
data = data.set_index('Datetime')

# Run backtest
bt = Backtest(data, VoltaicBreakout, cash=1_000_000, commission=.002)
stats = bt.run()

print("🌕 Moon Dev Backtest Complete! 🌕")
print("=== Strategy Performance ===")
print(stats)
print("\n✨ Strategy Details ✨")
print(stats._strategy)