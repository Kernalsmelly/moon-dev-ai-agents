import pandas as pd
import talib
from backtesting import Backtest, Strategy

class QuadrupleHarmonic(Strategy):
    def init(self):
        self.ema = self.I(talib.EMA, self.data.Close, timeperiod=200)
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=50)  # 🌙 Added EMA50 for stronger uptrend confirmation in pullbacks
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)  # 🌙 Kept RSI for oversold filter in pullbacks
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=20)  # 🌙 Kept volume SMA for confirmation filter
        self.entry_bar = None  # To track for potential exits
        self.entry_price = None  # 🌙 Track entry price for trailing stop logic
        self.stop_distance = None  # 🌙 Track initial stop distance for trailing trigger

    def next(self):
        current_close = self.data.Close[-1]
        current_open = self.data.Open[-1]
        current_low = self.data.Low[-1]
        current_high = self.data.High[-1]
        
        # Debug print for trend context 🌙
        if len(self.data) % 100 == 0:
            print(f"🌙 Moon Dev Debug: Current Close {current_close:.2f}, EMA {self.ema[-1]:.2f}, EMA50 {self.ema50[-1]:.2f}, ADX {self.adx[-1]:.2f} ✨")
        
        # Position management with trailing stop 🌙
        if self.position:
            # Early exit if breaks below EMA200 (uptrend protection)
            if current_close < self.ema[-1]:
                self.position.close()
                print(f"🌙 Moon Dev Exit: Early close below EMA at {current_close:.2f} 🚀")
            
            # Trailing stop logic: after 1R profit, trail SL to 1 ATR below current close 🌙
            if self.entry_price and self.stop_distance:
                profit = current_close - self.entry_price
                if profit > self.stop_distance:
                    trail_sl = current_close - 1.0 * self.atr[-1]
                    if trail_sl > self.position.sl:
                        self.position.sl = trail_sl
                        print(f"🌙 Moon Dev Trail: Updated SL to {trail_sl:.2f} after 1R profit 🚀")
            
            # Time-based exit after 20 bars (increased from 15 for more room in volatile conditions) 🌙
            if self.entry_bar and len(self.data) - self.entry_bar > 20:
                self.position.close()
                print(f"🌙 Moon Dev Exit: Time-based close after 20 bars at {current_close:.2f} ✨")
            return
        
        # Entry logic only if no position
        if self.position or len(self.data) < 200:  # 🌙 Kept 200 for EMA200 maturity
            return
        
        # Calculate bullish candle strength (tightened to >60% body for higher quality reversals) 🌙
        body = current_close - current_open
        candle_range = current_high - current_low
        strong_bullish = body > 0.6 * candle_range and body > 0  # 🌙 Tightened from 0.5 to 0.6 for stronger confirmation
        
        # Check for previous 3 consecutive down bars (loosened from 4 to capture more pullback opportunities) and current bullish confirmation in uptrend 🌙
        if (len(self.data) >= 4 and
            self.data.Close[-4] < self.data.Open[-4] and
            self.data.Close[-3] < self.data.Open[-3] and
            self.data.Close[-2] < self.data.Open[-2] and
            self.data.Close[-3] < self.data.Close[-4] and
            self.data.Close[-2] < self.data.Close[-3] and
            strong_bullish and  # Bullish confirmation candle with strength filter
            current_close > self.ema50[-1] and self.ema50[-1] > self.ema[-1] and  # 🌙 Enhanced uptrend filter: above EMA50 and EMA50 > EMA200 for quality trends
            self.adx[-1] > 22 and  # 🌙 Slightly raised ADX from 20 to 22 for stronger trend quality
            self.rsi[-1] < 45 and  # 🌙 Loosened RSI from <40 to <45 for more pullback signals without being too oversold
            self.data.Volume[-1] > 1.2 * self.vol_sma[-1]):  # 🌙 Loosened volume filter from 1.5x to 1.2x for more opportunities while keeping conviction
            
            print(f"🌙 Moon Dev Signal: 3 down bars pullback in strong uptrend, strong bullish confirmation at {current_close:.2f} 🚀")
            
            # Pattern details - adjusted min_low for 3 down bars 🌙
            pattern_lows = self.data.Low[-4:-1]  # Lows of the 3 down bars
            min_low = min(pattern_lows)
            
            # SL: below pattern low by 1 ATR (conservative placement)
            atr_val = self.atr[-1]
            sl_price = min_low - 1.0 * atr_val
            
            # Entry at current close (approximate)
            entry_price = current_close
            
            # Improved TP: 1:3 Risk-Reward ratio for higher return potential 🌙
            stop_distance = entry_price - sl_price
            if stop_distance <= 0:
                print("🌙 Moon Dev Skip: Zero or negative stop distance 😔")
                return
            tp_price = entry_price + 3 * stop_distance  # 🌙 Increased RR from 2 to 3 for better average wins
            
            # Risk management: 1% risk, compute as fraction of equity 🌙
            risk_pct = 0.01
            size_frac = risk_pct * (entry_price / stop_distance)
            size = min(size_frac, 0.5)  # Cap at 50% exposure for risk management in volatile BTC
            
            if size > 0:
                # Debug print before order 🌙
                print(f"🌙 Moon Dev Debug: Order params - Size {size:.4f}, SL {sl_price:.5f}, TP {tp_price:.5f}, Entry/Close {entry_price:.5f} ✨")
                # Market entry at next open (improves fill rate in gappy crypto) 🌙
                self.buy(size=size, sl=sl_price, tp=tp_price)
                self.entry_bar = len(self.data)
                self.entry_price = self.position.price  # 🌙 Set for trailing
                self.stop_distance = stop_distance  # 🌙 Set for trailing trigger
                print(f"🌙 Moon Dev Entry: Long {size:.4f} fraction at ~{entry_price:.2f}, SL {sl_price:.2f}, TP {tp_price:.2f} (Min Low {min_low:.2f}) 🚀✨")

# Data loading and cleaning
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path)
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.set_index(pd.to_datetime(data['datetime']))
data = data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})

# Ensure required columns exist
required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
for col in required_cols:
    if col not in data.columns:
        print(f"Warning: Column {col} missing!")

# Run backtest
bt = Backtest(data, QuadrupleHarmonic, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)
print(stats._strategy)