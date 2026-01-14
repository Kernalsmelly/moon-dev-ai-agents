# 🌙 Moon Dev Backtest Engine: BandwidthSurge Strategy Implementation 🚀
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class BandwidthSurge(Strategy):
    def init(self):
        # 🌌 Cosmic Indicators Configuration
        self.risk_percent = 0.01  # 1% risk per trade
        
        # 🌀 Bollinger Bands Calculation
        def bb_upper(close): return talib.BBANDS(close, 20, 2, 2)[0]
        def bb_lower(close): return talib.BBANDS(close, 20, 2, 2)[2]
        self.upper_band = self.I(bb_upper, self.data.Close)
        self.lower_band = self.I(bb_lower, self.data.Close)
        
        # 📡 Bandwidth Calculation (Upper-Lower)/Middle
        middle_band = self.I(lambda close: talib.BBANDS(close, 20, 2, 2)[1], self.data.Close)
        self.bandwidth = self.I(lambda u,l,m: (u-l)/m, self.upper_band, self.lower_band, middle_band)
        
        # 🌟 6-Month Low Filter (17280 periods for 15m data)
        self.bandwidth_low = self.I(talib.MIN, self.bandwidth, 17280)
        
        # 📈 Volume Surge Indicator
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 4800)  # 50-day SMA
        
        # 🌪️ ADX Trend Filter
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, 14)
        
        # 🛑 Parabolic SAR Exit
        self.sar = self.I(talib.SAR, self.data.High, self.data.Low, 0.02, 0.2)

    def next(self):
        # 🌙 Core Trading Logic
        if not self.position:
            # 🚀 Entry Conditions
            bandwidth_at_low = self.bandwidth[-1] <= self.bandwidth_low[-1]
            price_breakout = self.data.Close[-1] > self.upper_band[-1]
            volume_surge = self.data.Volume[-1] > self.volume_sma[-1] * 1.2
            low_trend_strength = self.adx[-1] < 20
            
            if all([bandwidth_at_low, price_breakout, volume_surge, low_trend_strength]):
                # 💰 Risk Management Calculation
                risk_amount = self.equity * self.risk_percent
                entry_price = self.data.Close[-1]
                stop_loss = entry_price * 0.98  # 2% stop loss
                position_size = int(round(risk_amount / (entry_price - stop_loss)))
                
                if position_size > 0:
                    self.buy(size=position_size, sl=stop_loss)
                    print("🌕 MOON DEV ALERT: Cosmic Breakout Detected! 🚀")
                    print(f"Entry: {entry_price:.2f} | Size: {position_size} shares")
        
        else:
            # 🌠 Exit Conditions
            if self.sar[-1] > self.data.Close[-1]:
                self.position.close()
                print("🌑 SAR Reversal Signal: Returning to Base Station 🛸")

# 🛰️ Data Loading & Preparation
data = pd.read_csv("/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv")

# 🧹 Data Cleaning Ritual
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'datetime': 'Date',
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}).set_index('Date')

# 🌍 Launch Backtest Sequence
bt = Backtest(data, BandwidthSurge, cash=1_000_000, commission=.002)
stats = bt.run()

# 🪐 Display Cosmic Performance Report
print("\n🌌 FINAL MISSION STATISTICS 🌌")
print(stats)
print(stats._strategy)