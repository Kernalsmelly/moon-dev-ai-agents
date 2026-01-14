# 🌙 Moon Dev's VolatilityCompression Backtest 🌙
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import talib

class VolatilityCompression(Strategy):
    risk_pct = 0.01  # 1% risk per trade 🌙
    lookback = 20
    swing_period = 20
    ema_period = 5
    
    def init(self):
        # 🌟 Calculate volatility components
        self.realized_vol = self.I(talib.STDDEV, self.data.Close, timeperiod=20, name='Realized Vol')
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14, name='ATR')
        self.vix_ratio = self.I(lambda: self.atr/self.realized_vol, name='VIX Ratio')
        
        # 🚀 Mean reversion components
        self.ratio_mean = self.I(talib.SMA, self.vix_ratio, self.lookback, name='Ratio Mean')
        self.ratio_std = self.I(talib.STDDEV, self.vix_ratio, self.lookback, name='Ratio STD')
        self.ratio_ema = self.I(talib.EMA, self.vix_ratio, self.ema_period, name='Ratio EMA')
        
        # 📊 Volume confirmation
        self.volume_ma = self.I(talib.SMA, self.data.Volume, 20, name='Volume MA')
        
        # 🛑 Swing low for stop loss
        self.swing_low = self.I(talib.MIN, self.data.Low, self.swing_period, name='Swing Low')
        
        print("🌙 Moon Dev Indicators Activated! Ready for Launch 🚀")

    def next(self):
        price = self.data.Close[-1]
        
        if self.position:
            # 🌗 Exit on EMA crossover
            if crossover(self.ratio_ema, self.vix_ratio):
                self.position.close()
                print(f"🌙 Moon Exit Signal! Closing at {price:.2f} ✨")
        else:
            # 🌌 Entry conditions
            upper_band = self.ratio_mean[-1] + 2*self.ratio_std[-1]
            
            if (self.vix_ratio[-1] > upper_band and 
                self.data.Volume[-1] < self.volume_ma[-1]):
                
                # 🎯 Calculate position size
                sl = self.swing_low[-1]
                risk_per_share = price - sl
                if risk_per_share <= 0: return
                
                equity_risk = self.risk_pct * self.equity
                position_size = int(round(equity_risk / risk_per_share))
                
                if position_size > 0:
                    self.buy(size=position_size, sl=sl)
                    print(f"🚀 MOON SHOT! Long {position_size} @ {price:.2f} ✨")

# 🌕 Data Preparation
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
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

# 🌑 Launch Backtest
bt = Backtest(data, VolatilityCompression, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌠 Print Moon Results
print("\n🌙🌙🌙 FULL MOON STATS 🌙🌙🌙")
print(stats)
print("\n🚀 STRATEGY METRICS 🚀")
print(stats._strategy)