# 🌙 MOON DEV BACKTESTING IMPLEMENTATION FOR LIQUIDATION REVERSION STRATEGY 🚀

import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import talib

# ========================
# DATA PREPARATION 🌌
# ========================
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')

# Clean and prepare cosmic data 📡
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

# ========================
# STRATEGY CLASS 🌗
# ========================
class LiquidationReversion(Strategy):
    risk_percent = 0.01  # 1% cosmic risk allocation 🌠
    stop_loss_pct = 0.03  # 3% black hole protection 🕳️
    bb_period = 80  # 20-period 1h BB (80*15m=20h) 🌐
    sma_period = 20  # Mean reversion target 🎯
    
    def init(self):
        # Cosmic indicators initialization 🌟
        self.bb_lower = self.I(lambda close: talib.BBANDS(close, self.bb_period, 2, 2)[2], 
                              self.data.Close, name='BB_LOWER')
        self.sma20 = self.I(talib.SMA, self.data.Close, self.sma_period, name='SMA20')
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 20, name='VOLUME_SMA')
        
    def next(self):
        current_price = self.data.Close[-1]
        bb_lower = self.bb_lower[-1]
        sma20 = self.sma20[-1]
        
        # 🌙 MOON DEV DEBUG CONSOLE ✨
        print(f"🌌 Price: {current_price:.2f} | BB Lower: {bb_lower:.2f} | SMA20: {sma20:.2f}")
        print(f"📡 Volume SMA: {self.volume_sma[-1]:.2f} vs Current: {self.data.Volume[-1]:.2f}")

        # 🚀 ENTRY SIGNAL: 3x Below Cosmic Floor 🌠
        if not self.position and current_price < 0.97 * bb_lower:
            risk_amount = self.equity * self.risk_percent
            stop_price = current_price * (1 - self.stop_loss_pct)
            risk_per_share = current_price - stop_price
            
            if risk_per_share <= 0:
                print("🛑 ABORT: Negative risk detected!")
                return
                
            position_size = int(round(risk_amount / risk_per_share))
            
            if position_size > 0:
                self.buy(size=position_size, sl=stop_price, tp=sma20)
                print(f"🚀🌕 MOONSHOT ENTRY! Size: {position_size} | Entry: {current_price:.2f}")
                print(f"🔐 Stop: {stop_price:.2f} | Target: {sma20:.2f}")

        # 🌑 EXIT SIGNAL: Reaching Stellar SMA with Volume Surge 🌊
        if self.position and crossover(self.data.Close, self.sma20):
            if self.data.Volume[-1] > self.volume_sma[-1]:
                self.position.close()
                print(f"🌗🌠 COSMIC EXIT! Price {current_price:.2f} reached SMA20 with Volume Boost")

# ========================
# LAUNCH BACKTEST 🚀
# ========================
bt = Backtest(data, LiquidationReversion, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌌 FINAL MISSION REPORT 📊
print("\n=== MOON DEV FINAL STATS ===")
print(stats)
print(stats._strategy)