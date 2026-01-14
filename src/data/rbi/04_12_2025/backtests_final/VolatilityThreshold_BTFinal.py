import pandas as pd
import talib
from backtesting import Strategy, Backtest

# Moon Dev data preparation 🌙
try:
    data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
    print("🌕 Data loaded successfully! Beginning celestial analysis...")
except FileNotFoundError:
    raise FileNotFoundError("🚨 Lunar data not found! Please check the file path.")

# Clean and prepare data
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col], errors='ignore')
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class VolatilityThreshold(Strategy):
    risk_percent = 0.01  # 1% risk per trade 🌕
    
    def init(self):
        # Calculate indicators using TA-Lib 🌙
        self.atr_window = 20 * 24 * 4  # 20 days in 15m periods
        self.sma_window = 50 * 24 * 4  # 50 days in 15m periods
        
        # Core indicators ✨
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=self.atr_window)
        self.sma50 = self.I(talib.SMA, self.data.Close, timeperiod=self.sma_window)
        
        # Historical ATR percentile calculation 🌌
        self.atr_percentile = self.I(
            lambda s: s.expanding().quantile(0.10),
            self.atr
        )
        print("🌙 Indicators initialized! Ready for lunar trading cycles...")

    def next(self):
        # Wait for indicators to warm up 🕒
        if len(self.data) < self.sma_window or pd.isna(self.atr[-1]) or pd.isna(self.atr_percentile[-1]):
            return

        current_close = self.data.Close[-1]
        current_atr = self.atr[-1]
        atr_threshold = self.atr_percentile[-1]
        sma_value = self.sma50[-1]

        # Moon Dev entry logic 🌙🚀
        if not self.position:
            if current_close > sma_value and current_atr < atr_threshold:
                # Risk management calculations 🔒
                risk_amount = self.equity * self.risk_percent
                stop_distance = 2 * current_atr
                position_size = risk_amount / stop_distance
                position_size = int(round(position_size))  # Ensure whole units 🌕
                
                if position_size > 0:
                    print(f"🌕✨ MOON SHOT! Entry at {current_close:.2f}")
                    print(f"   ATR({self.atr_window}): {current_atr:.2f} < Percentile: {atr_threshold:.2f}")
                    print(f"   Position size: {position_size} units")
                    self.buy(size=position_size, sl=current_close - stop_distance)  # Set stop loss price level

        # Exit logic 🌘
        elif self.position:
            position = self.position
            entry_price = position.entry_price
            bars_held = len(self.data) - position.entry_bar - 1
            
            # Time-based exit ⏳
            if bars_held >= 5 * 24 * 4:  # 5 days in 15m periods
                print(f"⏰🌓 TIME EXIT! Held {bars_held} bars")
                self.position.close()

# Launch backtest 🚀🌙
print("🌌 Beginning lunar backtest sequence...")
bt = Backtest(data, VolatilityThreshold, cash=1_000_000, commission=.00075, trade_on_close=False)
stats = bt.run()
print("\n🌠 Backtest complete! Lunar performance report:")
print(stats)
print(stats._strategy)