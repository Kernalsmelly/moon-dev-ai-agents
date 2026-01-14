# 🌙 Moon Dev Bearish Breakout Backtest 🌙
import pandas as pd
import talib
from backtesting import Backtest, Strategy

# 🚀 Data Preparation
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean and prepare data
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
data = data.set_index('datetime')

class BearishBreakout(Strategy):
    risk_percent = 0.01  # 🌑 1% risk per trade
    
    def init(self):
        # 🌓 Calculate indicators using TA-Lib
        self.engulfing = self.I(talib.CDLENGULFING, 
                              self.data.Open, self.data.High, 
                              self.data.Low, self.data.Close)
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 20)
        self.current_engulfing_low = None
        self.current_engulfing_high = None

    def next(self):
        # 🌗 Check for new bearish engulfing patterns
        if self.engulfing[-1] == 100:
            self.current_engulfing_low = self.data.Low[-1]
            self.current_engulfing_high = self.data.High[-1]
            print(f"🌑 Bearish Engulfing Pattern Detected! | Low: {self.current_engulfing_low:.2f} | High: {self.current_engulfing_high:.2f}")

        # 🌓 Entry Logic
        if self.current_engulfing_low and not self.position:
            if (self.data.Low[-1] < self.current_engulfing_low and 
                self.data.Volume[-1] > self.volume_sma[-1]):
                
                # 🌔 Risk Management Calculations
                risk_amount = self.equity * self.risk_percent
                entry_price = self.data.Close[-1]
                stop_loss = self.current_engulfing_high
                risk_per_unit = stop_loss - entry_price
                
                if risk_per_unit > 0:
                    position_size = risk_amount / risk_per_unit
                    position_size = int(round(position_size))
                    
                    if position_size > 0:
                        self.sell(size=position_size, 
                                 sl=stop_loss,
                                 tp=entry_price - 2*risk_per_unit)
                        print(f"🚀🌒 SHORT ENTRY | Size: {position_size} | Entry: {entry_price:.2f} | SL: {stop_loss:.2f} | TP: {entry_price-2*risk_per_unit:.2f}")

        # 🌓 Exit Logic
        if self.position and self.data.Close[-1] > self.current_engulfing_low:
            self.position.close()
            print(f"🌙✨ EXIT SIGNAL | Price closed above pattern low: {self.current_engulfing_low:.2f}")

# 🌕 Run Backtest
bt = Backtest(data, BearishBreakout, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌖 Print Full Statistics
print("\n🌕🌖🌗🌘🌑🌒🌓🌔🌕")
print("MOON DEV BACKTEST RESULTS:")
print(stats)
print(stats._strategy)
print("🌙 Happy Trading! 🌙")