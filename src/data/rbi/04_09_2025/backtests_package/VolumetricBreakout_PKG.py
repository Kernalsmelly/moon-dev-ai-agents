# 🌙 Moon Dev's Volumetric Breakout Backtest 🌙
from backtesting import Backtest, Strategy
import pandas as pd
import talib

# Clean and prepare cosmic data 🌌
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

class VolumetricBreakout(Strategy):
    risk_pct = 0.02  # 🛡️ 2% cosmic risk shield
    bb_period = 20    # 🌗 Bollinger Moon Cycles
    volume_days = 20  # 📅 20 Earth rotations
    
    def init(self):
        # 🪐 Calculate cosmic indicators
        self.volume_period = self.volume_days * 24 * 4  # 15min intervals
        
        # 🌗 Bollinger Bands with stardust calculations
        self.mid_band = self.I(talib.SMA, self.data.Close, timeperiod=self.bb_period)
        self.std = self.I(talib.STDDEV, self.data.Close, timeperiod=self.bb_period)
        self.upper_band = self.I(lambda: self.mid_band + 2*self.std, name='Upper Band')
        
        # 📈 Volume rocket fuel indicator
        self.vol_ma = self.I(talib.SMA, self.data.Volume, timeperiod=self.volume_period)
        
        print("🌌 Moon Dev Indicators Activated! Ready for Launch Sequence �✨")

    def next(self):
        price = self.data.Close[-1]
        upper = self.upper_band[-1]
        vol = self.data.Volume[-1]
        avg_vol = self.vol_ma[-1]
        
        # 🚀 Launch Conditions
        if not self.position and vol > 2*avg_vol and price > upper:
            # 🌕 Calculate meteor-resistant position size
            equity = self.equity
            risk_amount = equity * self.risk_pct
            sl_price = self.data.Low[-1]  # 🌑 Lunar surface stop
            
            if price <= sl_price:
                print(f"🚨 Moon Crash Alert! Aborting launch (Price {price:.2f} <= SL {sl_price:.2f})")
                return
                
            position_size = risk_amount / (price - sl_price)
            position_size = int(round(position_size))
            
            if position_size > 0:
                self.buy(size=position_size, sl=sl_price)
                print(f"🚀🌕 LIFTOFF! Buying {position_size} @ {price:.2f}")
                print(f"   🌠 Vol {vol:.2f} > 2x {avg_vol:.2f}, Upper Band {upper:.2f}")

        # 🌑 Re-entry Conditions
        elif self.position and price < self.mid_band[-1]:
            self.position.close()
            print(f"🌘🌍 Re-entry Signal! Closing @ {price:.2f} (Below SMA {self.mid_band[-1]:.2f})")

# 🌟 Execute Moon Mission
bt = Backtest(data, VolumetricBreakout, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌕 Print Cosmic Performance Report
print("\n🌙✨ Moon Dev Mission Report ✨🌙")
print(stats)
print(stats._strategy)