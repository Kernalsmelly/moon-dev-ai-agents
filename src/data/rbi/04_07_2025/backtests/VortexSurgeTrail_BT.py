import pandas as pd
import talib
import pandas_ta as ta
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

# Clean and prepare data
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')

# Clean columns
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class VortexSurgeTrail(Strategy):
    vi_period = 14
    vol_ma_period = 20
    ci_threshold = 38.2
    ci_length = 14
    atr_period = 14
    risk_pct = 0.02
    
    def init(self):
        # Vortex Indicator 🌪️
        h, l, c = self.data.High, self.data.Low, self.data.Close
        self.vi_plus, self.vi_minus = self.I(talib.VORTEX, h, l, c, self.vi_period, 
                                            name=['VI+', 'VI-'], plot=True)
        
        # Volume Surge 📈
        self.vol_ma = self.I(talib.SMA, self.data.Volume, self.vol_ma_period, 
                            name='Volume MA', color='purple')
        
        # Choppiness Index 🌀
        h_series = pd.Series(h)
        l_series = pd.Series(l)
        c_series = pd.Series(c)
        ci = ta.choppiness(h_series, l_series, c_series, length=self.ci_length)
        self.ci = self.I(lambda: ci, name='Choppiness', color='orange')
        
        # ATR for Stops 🔐
        self.atr = self.I(talib.ATR, h, l, c, self.atr_period, name='ATR')

    def next(self):
        price = self.data.Close[-1]
        
        # Moon Dev Entry Logic 🌙🚀
        if not self.position:
            if crossover(self.vi_plus, self.vi_minus):
                if self.data.Volume[-1] > self.vol_ma[-1]:
                    equity = self.equity
                    risk_amount = equity * self.risk_pct
                    atr_val = self.atr[-1] or 1
                    stop_dist = 2 * atr_val
                    
                    position_size = risk_amount / stop_dist
                    position_size = int(round(position_size))
                    
                    if position_size > 0:
                        self.buy(size=position_size, 
                                sl=price - stop_dist,
                                tag="MoonBeam Entry")
                        print(f"🌙✨🚀 LUNAR LIFT-OFF! Buying {position_size} units at {price:.2f}")

        # Moon Dev Exit Logic 🌙🌑
        else:
            if self.ci[-1] < self.ci_threshold:
                self.position.close()
                print(f"🌀🌑 VORTEX COLLAPSE! Exiting at {price:.2f}")
            elif self.data.Low[-1] <= self.position.sl:
                self.position.close()
                print(f"🛑🌒 STARSHIP HALT! Stop loss triggered at {price:.2f}")

# Launch Backtest 🚀
bt = Backtest(data, VortexSurgeTrail, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)