import pandas as pd
from backtesting import Backtest, Strategy
import talib

# Data preprocessing 🌙
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean and prepare cosmic data 📡
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data = data.rename(columns={
    'datetime': 'datetime',
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class VolatilityDivergence(Strategy):
    risk_pct = 0.02  # 2% per trade 🌙
    entry_threshold = 10  # 10% spread 🚀
    exit_threshold = 5   # 5% spread ✨
    atr_period_short = 14
    atr_period_long = 28
    
    def init(self):
        # Cosmic indicators 🌌
        self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 
              timeperiod=self.atr_period_short, name='ATR_14')
        self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close,
              timeperiod=self.atr_period_long, name='ATR_28')
        
        # Calculate moon phase spread 🌗
        def spread_calc(data):
            atr_14 = data['ATR_14']
            atr_28 = data['ATR_28']
            return ((atr_14 - atr_28) / atr_28) * 100
        self.I(spread_calc, name='Spread')
        
    def next(self):
        current_spread = self.data.Spread[-1]
        
        # Moon base exit check 🌑
        if self.position:
            if current_spread < self.exit_threshold:
                self.position.close()
                print(f"🌕 Moon Dev Alert: Cosmic spread normalized! ({current_spread:.2f}%) Closing position 🛸")
                
            # Stellar stop loss enforcement 🌠
            for trade in self.trades:
                if trade.is_short and trade.sl:
                    lunar_sl = trade.sl
                    if self.data.High[-1] >= lunar_sl:
                        print(f"🚨 Moon Dev Warning: Stellar stop loss triggered! ({self.data.High[-1]:.2f}) 🌪️")
        
        # Lunar entry signal 🌙
        else:
            if current_spread > self.entry_threshold:
                atr = self.data['ATR_14'][-1]
                entry_price = self.data.Close[-1]
                sl_price = entry_price + 2 * atr
                risk_amount = self.risk_pct * self.equity
                risk_per_unit = sl_price - entry_price
                
                if risk_per_unit > 0:
                    position_size = int(round(risk_amount / risk_per_unit))
                    if position_size > 0:
                        # Initiate moon mission 🚀
                        self.sell(size=position_size, sl=sl_price, 
                                 tp=entry_price - atr,
                                 tag=f"LunarSpread_{current_spread:.1f}%")
                        print("🌘 Moon Dev Signal: Volatility divergence detected! 🌗")
                        print(f"   Entry: {entry_price:.2f} | Size: {position_size}")
                        print(f"   SL: {sl_price:.2f} | Risk: {self.risk_pct*100:.0f}% of equity 🌌")

# Launch moon mission 🌕
bt = Backtest(data, VolatilityDivergence, cash=1_000_000, commission=.002)
stats = bt.run()

# Print cosmic performance report 🌟
print("\n🌌🌙🌌🌙🌌 MOON DEV BACKTEST RESULTS 🌌🌙🌌🌙🌌")
print(stats)
print("\n🌠 Cosmic Strategy Details:")
print(stats._strategy)