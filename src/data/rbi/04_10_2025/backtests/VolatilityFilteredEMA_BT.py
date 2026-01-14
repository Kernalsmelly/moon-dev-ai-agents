# 🌙 Moon Dev Backtest AI Generated Code - VolatilityFilteredEMA Strategy
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import pandas as pd
import talib

class VolatilityFilteredEMA(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade
    stop_loss_pct = 0.03   # 3% stop loss
    vix_entry_threshold = 15
    vix_exit_threshold = 20
    
    def init(self):
        # 🌙 Calculate core indicators using TA-Lib
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=50, name='EMA50')
        self.ema200 = self.I(talib.EMA, self.data.Close, timeperiod=200, name='EMA200')
        
        # 🌟 Calculate volatility index (VIX substitute using normalized ATR)
        def calculate_vix(high, low, close):
            atr = talib.ATR(high, low, close, timeperiod=14)
            return (atr / close) * 100  # Normalize ATR to percentage
            
        self.vix = self.I(calculate_vix, self.data.High, self.data.Low, self.data.Close, name='VIX_Substitute')
        
        print("🌙✨ Moon Dev System Initialized! Tracking EMA Crosses with Volatility Filtering...")

    def next(self):
        # 🚀 Moon Dev Trading Logic Core
        price = self.data.Close[-1]
        
        # Golden/Death Cross Detection
        golden_cross = crossover(self.ema50, self.ema200)
        death_cross = crossover(self.ema200, self.ema50)
        
        # Current volatility state
        current_vix = self.vix[-1] if len(self.vix) > 0 else 100
        
        # 🌙 Entry Logic: Golden Cross + Low Volatility
        if not self.position:
            if golden_cross and current_vix < self.vix_entry_threshold:
                # 🚨 Risk Management Calculation
                stop_price = price * (1 - self.stop_loss_pct)
                risk_per_share = price - stop_price
                position_size = (self.equity * self.risk_per_trade) / risk_per_share
                position_size = int(round(position_size))
                
                if position_size > 0:
                    self.buy(size=position_size, sl=stop_price, 
                            tag=f"MoonDevEntry_{price:.2f}")
                    print(f"🌙✨🚀 MOON DEV ALERT: LONG {position_size} @ {price:.2f} | EMA Cross + Calm Markets! 🌤️")
        
        # 🌧️ Exit Logic: Death Cross or Volatility Spike
        else:
            if death_cross or current_vix > self.vix_exit_threshold:
                self.position.close()
                exit_reason = "Volatility Spike" if current_vix > self.vix_exit_threshold else "Trend Reversal"
                print(f"🌙✨🚨 MOON DEV EXIT: Closing @ {price:.2f} | {exit_reason}! 🌩️")

# 🌍 Data Preparation Ritual
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path)

# 🧹 Cleanse Data Columns
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])

# 📊 Proper Column Mapping
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# ⏰ Set Temporal Index
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

# 🚀 Launch Moon Dev Backtest
bt = Backtest(data, VolatilityFilteredEMA, cash=1_000_000, margin=1.0)
stats = bt.run()

# 📜 Print Sacred Scrolls of Wisdom (Full Stats)
print("\n🌕🌖🌗🌘🌑🌒🌓🌔🌕 MOON DEV FINAL REPORT 🌕🌖🌗🌘🌑🌒🌓🌔🌕")
print(stats)
print(stats._strategy)