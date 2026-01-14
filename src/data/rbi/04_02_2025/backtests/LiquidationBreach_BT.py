# 🌙 Moon Dev's LiquidationBreach Backtest Script
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class LiquidationBreach(Strategy):
    risk_pct = 0.01  # 1% risk per trade 🌕
    rvol_threshold = 2.0
    breach_pct = 0.05
    max_holding_bars = 16  # 4 hours in 15m intervals
    
    def init(self):
        # 🌙 Moon Indicators
        self.support = self.I(talib.MIN, self.data.Low, 20, name='SUPPORT_20')
        self.resistance = self.I(talib.MAX, self.data.High, 20, name='RESISTANCE_20')
        self.next_support = self.I(talib.MIN, self.data.Low, 40, name='SUPPORT_40')
        self.next_resistance = self.I(talib.MAX, self.data.High, 40, name='RESISTANCE_40')
        
        # ✨ Volume Analysis
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 30, name='VOL_SMA30')
        self.rvol = self.I(lambda v, sma: v/sma, self.data.Volume, self.volume_sma, name='RVOL')
        self.volume_confirmation = self.I(talib.SMA, self.data.Volume, 5, name='VOL_CONFIRM')

    def next(self):
        # 🌑 Moon Position Management
        if self.position:
            # RVOL Spike Exit
            if self.rvol[-1] > self.rvol_threshold:
                self.position.close()
                print(f"🌕 Moon Exit: RVOL Spike {self.rvol[-1]:.2f}x! Banking Moonshots 🚀")
                return
            
            # Time-based Exit
            if len(self.data) - self.position.entry_bar >= self.max_holding_bars:
                self.position.close()
                print("🌗 Time Exit: 4hr Limit Reached. Returning to Orbit 🕒")
                return
            
        else:
            # 🚀 Long Entry: 5% Below Support with Volume Confirmation
            if self.data.Close[-1] <= self.support[-1] * (1 - self.breach_pct):
                if self.data.Volume[-1] > self.volume_confirmation[-1]:
                    sl_price = self.next_support[-1] * 0.99
                    risk_per_share = self.data.Close[-1] - sl_price
                    if risk_per_share > 0:
                        position_size = int(round((self.risk_pct * self.equity) / risk_per_share))
                        self.buy(size=position_size, sl=sl_price)
                        print(f"🚀 BLASTOFF LONG! Size: {position_size} | SL: {sl_price:.1f} 🌙")

            # 🌒 Short Entry: 5% Above Resistance with Volume Confirmation
            elif self.data.Close[-1] >= self.resistance[-1] * (1 + self.breach_pct):
                if self.data.Volume[-1] > self.volume_confirmation[-1]:
                    sl_price = self.next_resistance[-1] * 1.01
                    risk_per_share = sl_price - self.data.Close[-1]
                    if risk_per_share > 0:
                        position_size = int(round((self.risk_pct * self.equity) / risk_per_share))
                        self.sell(size=position_size, sl=sl_price)
                        print(f"🌚 SHORT LAUNCH! Size: {position_size} | SL: {sl_price:.1f} 🌌")

# 🌟 Moon Data Preparation
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
                   parse_dates=['datetime'], index_col='datetime')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

# 🌕 Launch Backtest
bt = Backtest(data, LiquidationBreach, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)