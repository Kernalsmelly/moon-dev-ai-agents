# 🌙 Moon Dev's Momentum Reversal Backtest 🌙
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import talib

class MomentumReversal(Strategy):
    risk_per_trade = 0.01  # 1% of equity per trade
    stop_loss_pct = 0.02   # 2% stop loss
    
    def init(self):
        # 🌟 Cosmic Indicators 🌟
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=5, name='RSI_5')
        self.ema5 = self.I(talib.EMA, self.data.Close, timeperiod=5, name='EMA_5')
        self.ema10 = self.I(talib.EMA, self.data.Close, timeperiod=10, name='EMA_10')
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=5, name='Volume_SMA')
        
        print("🌙✨ Strategy Initialized with Moon Power! ✨🌙")

    def next(self):
        current_close = self.data.Close[-1]
        current_rsi = self.rsi[-1]
        ema5 = self.ema5[-1]
        ema10 = self.ema10[-1]
        current_vol = self.data.Volume[-1]
        vol_sma = self.vol_sma[-1]

        # 🌙 Lunar Debug Console 🌙
        print(f"🌕 Close: {current_close:.2f} | RSI: {current_rsi:.1f} | EMA5/10: {ema5:.2f}/{ema10:.2f} | Vol: {current_vol:.2f} vs SMA: {vol_sma:.2f}")

        if not self.position:
            # 🚀 Launch Entry Conditions 🚀
            if current_rsi > 70 and ema5 > ema10:
                equity = self.equity
                entry_price = current_close
                sl_price = entry_price * (1 - self.stop_loss_pct)
                risk_amount = equity * self.risk_per_trade
                price_risk = entry_price - sl_price
                
                if price_risk <= 0:
                    print("🌑⚠️ Black Hole Alert! Negative risk detected!")
                    return
                
                position_size = int(round(risk_amount / price_risk))
                max_possible = int(equity // entry_price)
                position_size = min(position_size, max_possible)
                
                if position_size > 0:
                    self.buy(size=position_size, sl=sl_price)
                    print(f"🚀🌙 LIFTOFF! Long {position_size} @ {entry_price:.2f} | SL: {sl_price:.2f}")
                else:
                    print("🌑💸 Stardust Balance Too Low!")
        else:
            # 🛑 Cosmic Exit Conditions 🛑
            ema_cross = crossover(self.ema10, self.ema5)
            vol_decline = current_vol < vol_sma
            
            if ema_cross or vol_decline:
                self.position.close()
                print(f"🌙💫 REENTRY! Closing @ {current_close:.2f} | P/L: {self.position.pl:.2f}")

# 🌍 Data Preparation 🌍
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# 🧹 Data Cleansing Ritual 🧹
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col], errors='ignore')
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

print("🌙📊 Data Sanctified with Lunar Blessings! 📊🌙")

# 🚀 Launch Backtest 🚀
bt = Backtest(data, MomentumReversal, cash=1_000_000, exclusive_orders=True)
stats = bt.run()

# 🌟 Stellar Results 🌟
print("\n" + "🌠"*40)
print("🌙 MOON DEV FINAL MISSION REPORT 🌙")
print("🌠"*40 + "\n")
print(stats)
print(stats._strategy)