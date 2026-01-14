# 🌙 Moon Dev's FractalBreakout Backtest 🌙
from backtesting import Strategy
import pandas as pd
import talib
import pandas_ta as ta

# 🚀 DATA PREPARATION 
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

class FractalBreakout(Strategy):
    ema_period_fast = 20
    ema_period_slow = 50
    fractal_period = 20
    risk_per_trade = 0.02
    
    def init(self):
        # 🌌 FRACTAL INDICATORS
        self.fractal_high = self.I(ta.MAX, self.data.High, timeperiod=self.fractal_period, name='FRACTAL HIGH')
        self.fractal_low = self.I(ta.MIN, self.data.Low, timeperiod=self.fractal_period, name='FRACTAL LOW')
        
        # 🚀 MOMENTUM INDICATORS
        self.ema_fast = self.I(talib.EMA, self.data.Close, self.ema_period_fast, name='EMA20')
        self.ema_slow = self.I(talib.EMA, self.data.Close, self.ema_period_slow, name='EMA50')
        
        # 🎯 MEAN REVERSION LEVELS
        self.sma = self.I(talib.SMA, self.data.Close, 50, name='SMA50')
        self.std_dev = self.I(talib.STDDEV, self.data.Close, 50, name='STDDEV')

    def next(self):
        if len(self.data) < 50:  # Wait for indicators to warm up
            return
        
        # 🌙 CURRENT VALUES
        price = self.data.Close[-1]
        prev_fractal_high = self.fractal_high[-2]
        prev_fractal_low = self.fractal_low[-2]
        sma = self.sma[-1]
        std_dev = self.std_dev[-1]
        upper_band = sma + 1.5*std_dev
        lower_band = sma - 1.5*std_dev
        
        # 🚀 MOMENTUM CALCULATION
        momentum_bullish = (self.ema_fast[-2] < self.ema_slow[-2]) and (self.ema_fast[-1] > self.ema_slow[-1])
        momentum_bearish = (self.ema_fast[-2] > self.ema_slow[-2]) and (self.ema_fast[-1] < self.ema_slow[-1])

        # 💎 RISK MANAGEMENT
        if not self.position:
            # 🌌 LONG ENTRY: Breakout above fractal high with bullish momentum
            if price > prev_fractal_high and momentum_bullish:
                sl = prev_fractal_low
                risk_amount = self.risk_per_trade * self.equity
                risk_per_share = (sl - price)
                
                if risk_per_share > 0:
                    size = int(round(risk_amount / risk_per_share))
                    self.buy(size=size, sl=sl, tp=upper_band)
                    print(f"🚀🌙 LONG SIGNAL: {price:.2f} > {prev_fractal_high:.2f} | Size: {size} | SL: {sl:.2f} | TP: {upper_band:.2f}")
            
            # 🌑 SHORT ENTRY: Breakout below fractal low with bearish momentum
            elif price < prev_fractal_low and momentum_bearish:
                sl = prev_fractal_high
                risk_amount = self.risk_per_trade * self.equity
                risk_per_share = (sl - price)
                
                if risk_per_share > 0:
                    size = int(round(risk_amount / risk_per_share))
                    self.sell(size=size, sl=sl, tp=lower_band)
                    print(f"🌑🌙 SHORT SIGNAL: {price:.2f} < {prev_fractal_low:.2f} | Size: {size} | SL: {sl:.2f} | TP: {lower_band:.2f}")