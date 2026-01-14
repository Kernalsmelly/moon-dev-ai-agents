import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Load and preprocess data
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path)

# Clean and format data
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class VolumetricBollinger(Strategy):
    def init(self):
        # 🌙 Moon Dev Indicators 🌙
        self.sma20 = self.I(talib.SMA, self.data.Close, 20, name='SMA20')
        self.std20 = self.I(talib.STDDEV, self.data.Close, 20, 2, name='STD20')
        self.avg_volume = self.I(talib.SMA, self.data.Volume, 96, name='AvgVolume')
        
        # ✨ Strategy Parameters ✨
        self.risk_percent = 0.5
        self.entry_window_size = 2  # 30-minute window (2x15m)
        
    def next(self):
        # 🌕 Moon Dev Debug Prints 🌕
        if len(self.data) % 100 == 0:
            print(f"🌙 Processing bar {len(self.data)} | Price: {self.data.Close[-1]:.2f} | Equity: {self.equity:.2f}")

        if not self.position:
            # ✨ Calculate Conditions ✨
            bb_width = (4 * self.std20[-1]) / self.sma20[-1] if self.sma20[-1] else 0
            vol_condition = self.data.Volume[-1] >= 2 * self.avg_volume[-1]
            
            # 🚀 Entry Signal Detection 🚀
            if bb_width < 2.0 and vol_condition:
                print(f"✨🌟 SIGNAL DETECTED! | BB Width: {bb_width:.4f} | Volume: {self.data.Volume[-1]:.2f} (2x {self.avg_volume[-1]:.2f})")
                self.entry_window = self.entry_window_size
                
            # 🕒 Entry Window Handling 🕒
            if hasattr(self, 'entry_window') and self.entry_window > 0:
                price = self.data.Close[-1]
                position_size = int(round((self.equity * self.risk_percent) / price))
                
                if position_size > 0:
                    sl = price * 1.20
                    tp = price * 0.90
                    self.buy(size=position_size, sl=sl, tp=tp)
                    print(f"🚀🌕 ENTRY! {position_size} units @ {price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | Equity: {self.equity:.2f}")
                
                del self.entry_window
            elif hasattr(self, 'entry_window'):
                self.entry_window -= 1
                if self.entry_window <= 0:
                    del self.entry_window
                    print("🌑 Entry window closed without execution")

# 🌙 Backtest Execution 🌙
bt = Backtest(data, VolumetricBollinger, cash=1_000_000, commission=.002)
stats = bt.run()

# ✨ Results Print ✨
print("\n" + "="*50)
print("🌙🌕 MOON DEV FINAL BACKTEST RESULTS 🌕🌙")
print("="*50)
print(stats)
print(stats._strategy)