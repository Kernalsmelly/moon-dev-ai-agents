# 🌙 Moon Dev's DeltaDonchianBreakout Backtest 🌙
from backtesting import Backtest, Strategy
import pandas as pd
import talib
import pandas_ta as ta

# 🌌 Data Preparation Magic 🌌
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume',
    'datetime': 'DateTime'
}, inplace=True)
data['DateTime'] = pd.to_datetime(data['DateTime'])
data.set_index('DateTime', inplace=True)

class DeltaDonchianBreakout(Strategy):
    risk_pct = 0.01  # 🌙 1% Risk Per Trade
    
    def init(self):
        # 🌠 Indicator Constellation 🌠
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        volume = self.data.Volume
        
        # 🌗 Delta Volume Calculation
        delta_dir = (close > close.shift(1)).astype(int)*2 - 1
        self.delta_vol = self.I(lambda: delta_dir * volume, name='Delta_Volume')
        
        # 🌀 Donchian Channels
        self.donchian_low = self.I(talib.MIN, low, timeperiod=20, name='Donchian_Lower')
        self.donchian_high = self.I(talib.MAX, high, timeperiod=20, name='Donchian_Upper')
        
        # 🌊 Volume-Weighted MA
        self.vwma = self.I(ta.vwma, close, volume, length=20, name='VWMA')
        
    def next(self):
        price = self.data.Close[-1]
        
        # 🚨 Entry Conditions
        if not self.position:
            bearish_break = price < self.donchian_low[-1]
            volume_divergence = (self.delta_vol[-1] < self.delta_vol[-2] < 0)
            
            if bearish_break and volume_divergence:
                # 🌪️ Risk Management Calculations
                stop_price = self.donchian_high[-1]
                risk_per_share = stop_price - price
                
                if risk_per_share > 0:
                    equity_risk = self.equity * self.risk_pct
                    position_size = int(round(equity_risk / risk_per_share))
                    
                    if position_size > 0:
                        self.sell(size=position_size, sl=stop_price)
                        print(f"🌑🚀 BEARISH BREAKOUT! Short {position_size} @ {price:.2f} | SL: {stop_price:.2f}")
        
        # 💫 Exit Condition
        elif self.position and price > self.vwma[-1]:
            self.position.close()
            print(f"🌕✨ BULLISH RECOVERY! Closing @ {price:.2f} | VWMA: {self.vwma[-1]:.2f}")

# 🚀 Launch Backtest
bt = Backtest(data, DeltaDonchianBreakout, cash=1_000_000, commission=.002)
stats = bt.run()

# 📜 Moon Dev Performance Report 📜
print("\n" + "="*55 + "\n🌙 MOON DEV FINAL STATS 🌙\n" + "="*55)
print(stats)
print(f"\n✨ Strategy Details:\n{stats._strategy}")