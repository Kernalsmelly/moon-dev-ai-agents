# 🌙 Moon Dev's VolumeSilhouetteEMA Backtest 🌙
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import talib

class VolumeSilhouetteEMA(Strategy):
    risk_per_trade = 0.01  # 1% of equity
    rsi_exit_level = 70
    ema_period_short = 50
    ema_period_long = 200
    swing_low_period = 20
    
    def init(self):
        # 🌙 Calculate indicators using TA-Lib
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=self.ema_period_short)
        self.ema200 = self.I(talib.EMA, self.data.Close, timeperiod=self.ema_period_long)
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.swing_low = self.I(talib.MIN, self.data.Low, timeperiod=self.swing_low_period)
        
        # Volume analysis using 5-period MA
        self.vol_ma5 = self.I(talib.MA, self.data.Volume, timeperiod=5, name='Volume MA5')
        
    def next(self):
        # 🌙 MOON DEV STRATEGY LOGIC 🚀
        if not self.position:
            # Entry conditions
            ema_cross = crossover(self.ema50, self.ema200)
            vol_decline = (self.data.Volume[-3] > self.data.Volume[-2] and 
                          self.data.Volume[-2] > self.data.Volume[-1])
            
            if ema_cross and vol_decline:
                # Calculate risk parameters
                entry_price = self.data.Open[-1]
                sl_price = min(self.swing_low[-1], entry_price * 0.99)
                risk_amount = self.risk_per_trade * self.equity
                risk_per_share = entry_price - sl_price
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    tp_price = entry_price + 2 * (entry_price - sl_price)
                    
                    # 🌙 Execute moon-powered entry ✨
                    self.buy(size=position_size, 
                            sl=sl_price,
                            tp=tp_price)
                    
                    print("🌙🚀 MOON DEV ENTRY 🌙")
                    print(f"Size: {position_size} | Entry: {entry_price:.2f}")
                    print(f"🌑 SL: {sl_price:.2f} | 🚀 TP: {tp_price:.2f}")
        else:
            # Exit conditions
            if self.rsi[-1] > self.rsi_exit_level:
                self.position.close()
                print(f"🌙📉 RSI EXIT at {self.data.Close[-1]:.2f}")

# 🌙 DATA PREPARATION 🌙
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path)

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
data.set_index('datetime', inplace=True)

# 🌙 LAUNCH MOON BACKTEST 🚀
bt = Backtest(data, VolumeSilhouetteEMA, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌙 PRINT COSMIC RESULTS ✨
print("\n🌙🌙🌙 MOON DEV FINAL STATS 🌙🌙🌙")
print(stats)
print(stats._strategy)