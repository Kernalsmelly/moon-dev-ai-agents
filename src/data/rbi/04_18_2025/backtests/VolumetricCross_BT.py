# 🌙 MOON DEV BACKTESTING SCRIPT FOR VOLUMETRICCROSS STRATEGY 🚀

# ======== REQUIRED IMPORTS ========
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import talib

# ======== DATA PREPARATION ========
# Load and clean cryptocurrency data 🌌
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
                 parse_dates=['datetime'], index_col='datetime')

# Clean column names according to Moon Dev specifications 🧹
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

# ======== CORE STRATEGY CLASS ========
class VolumetricCross(Strategy):
    # Strategy parameters 🌗
    ema_short = 50
    ema_long = 200
    rsi_period = 14
    volume_ma_period = 20
    risk_per_trade = 0.01  # 1% risk per trade 🌕
    volume_spike = 1.5  # 150% of average volume 🌊
    stop_loss_pct = 0.02  # 2% stop loss 🛑

    def init(self):
        # Calculate indicators using TA-Lib with Moon Dev precision 🌙
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=self.ema_short, name='🌙 EMA50')
        self.ema200 = self.I(talib.EMA, self.data.Close, timeperiod=self.ema_long, name='🚀 EMA200')
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=self.rsi_period, name='✨ RSI14')
        self.vol_ma = self.I(talib.SMA, self.data.Volume, timeperiod=self.volume_ma_period, name='🌊 Volume MA20')

    def next(self):
        # Moon Dev's Galactic Entry Logic 🌌
        if not self.position:
            # EMA crossover condition with volume spike validation
            if crossover(self.ema50, self.ema200):
                current_volume = self.data.Volume[-1]
                avg_volume = self.vol_ma[-1]
                
                if current_volume > self.volume_spike * avg_volume and avg_volume > 0:
                    # Risk management calculations 🌗
                    entry_price = self.data.Close[-1]
                    stop_loss = entry_price * (1 - self.stop_loss_pct)
                    risk_amount = self.equity * self.risk_per_trade
                    risk_per_share = entry_price - stop_loss
                    
                    if risk_per_share > 0:
                        position_size = int(round(risk_amount / risk_per_share))
                        
                        if position_size > 0:
                            self.buy(size=position_size, sl=stop_loss, 
                                    tag=f"🌙✨ Golden Cross Detected | Volume Spike {current_volume/avg_volume:.1f}x")
                            print(f"🚀🌕 MOON DEV ENTRY! | Size: {position_size} | Entry: {entry_price:.2f} | SL: {stop_loss:.2f}")

        # Cosmic Exit Logic 🌠
        else:
            if self.rsi[-1] >= 70:
                self.position.close()
                print(f"🌑✨ MOON DEV EXIT! | Price: {self.data.Close[-1]:.2f} | RSI: {self.rsi[-1]:.2f}")

# ======== BACKTEST EXECUTION ========
# Initialize lunar backtest 🌕
bt = Backtest(data, VolumetricCross, cash=1_000_000, exclusive_orders=True)

# Launch Moon Dev analysis 🚀
stats = bt.run()
print("\n🌙✨🌕 FULL MOON STATISTICS 🌕✨🌙")
print(stats)
print(stats._strategy)