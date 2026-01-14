# 🌙 Moon Dev Backtest AI Generated Code 🚀
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class VolatilityDecay(Strategy):
    atr_period = 14
    iv_lookback = 672  # 1 week in 15m intervals (7*24*4)
    hold_period = 480   # 5 days in 15m (5*24*4)
    risk_pct = 0.01     # 1% risk per trade

    def init(self):
        # 🌗 Moon Phase 1: Volatility Calculation
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, self.atr_period)
        self.atr_sma = self.I(talib.SMA, self.atr, timeperiod=self.iv_lookback)
        self.iv_pct_change = (self.atr / self.atr_sma - 1) * 100

        # 🌒 Debugging Series
        self.decision_points = []

    def next(self):
        # 🌑 Moon Phase 2: Strategy Logic
        if len(self.data) < self.iv_lookback:
            return

        current_iv = self.iv_pct_change[-1]
        entry_condition = current_iv <= -3

        # 🌓 Moon Phase 3: Entry Logic
        if not self.position and entry_condition:
            equity = self.equity
            entry_price = self.data.Close[-1]
            stop_loss = entry_price * 1.01  # 1% buffer for SL
            
            # 🌙 Position Sizing Fix: Ensure whole number units
            risk_per_share = abs(stop_loss - entry_price)
            position_size = (equity * self.risk_pct) / risk_per_share
            position_size = int(round(position_size))  # Round to whole units

            if position_size > 0:
                self.sell(size=position_size, sl=stop_loss, tag='MOON_ENTRY')
                self.entry_bar = len(self.data) - 1
                print(f"🌙✨ Moon Dev Alert: Shorting {position_size} units at {entry_price:.2f}! SL at {stop_loss:.2f} 🚀")

        # 🌔 Moon Phase 4: Exit Logic
        if self.position:
            bars_held = len(self.data) - 1 - self.entry_bar
            if bars_held >= self.hold_period:
                self.position.close()
                print(f"🌕🕒 Moon Dev Exit: Hold period expired after {self.hold_period} bars!")

# 🌍 Data Preparation Ritual
try:
    data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
except FileNotFoundError:
    print("🌑 ERROR: Moon Data File Not Found! Please check the file path.")
    exit()

# 🧹 Moon Cleansing Ceremony
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume',
    'datetime': 'DateTime'
}, inplace=True)

# 🌙 DateTime Conversion Fix
try:
    data['DateTime'] = pd.to_datetime(data['DateTime'])
    data.set_index('DateTime', inplace=True)
except Exception as e:
    print(f"🌒 ERROR: Moon Time Conversion Failed! {str(e)}")
    exit()

# 🚀 Moon Launch Sequence
bt = Backtest(data, VolatilityDecay, cash=1_000_000, commission=.002)
stats = bt.run()

# 📊 Moon Analytics Portal
print("\n🌕🌖🌗🌘🌑🌒🌓🌔 MOON DEV FINAL STATS 🌔🌓🌒🌑🌘🌗🌖🌕")
print(stats)
print(stats._strategy)