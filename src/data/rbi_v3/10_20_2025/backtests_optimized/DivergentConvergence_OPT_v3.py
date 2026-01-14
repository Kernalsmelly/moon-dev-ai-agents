import pandas as pd
import talib
import numpy as np
from backtesting import Backtest, Strategy

# Data loading and cleaning
path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(path)
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'date': 'datetime',
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})
data = data.set_index(pd.to_datetime(data['datetime'])).drop(columns=['datetime'])
print("🌙 Moon Dev: Data loaded and cleaned successfully! Columns: Open, High, Low, Close, Volume. Index: Datetime. ✨")

class DivergentConvergence(Strategy):
    pivot_window = 5  # Not used, using approximation
    lookback = 25  # Increased lookback for more robust divergence detection 🌙
    recent_period = 12  # Adjusted recent period to capture better recent lows ✨
    stoch_window = 8  # Slightly increased for smoother stochastic convergence signals 🚀
    risk_pct = 0.015  # Increased risk per trade slightly for higher exposure while managing risk 📈
    rr = 3  # Improved RR to 3:1 for capturing larger profits on winning trades 💰
    max_bars = 25  # Extended max hold time to allow trends to develop further ⏱️
    atr_mult_sl = 1.5  # Kept but now used in trailing for consistency
    buffer_mult = 0.3  # Reduced buffer for tighter initial SL to improve risk-reward 🎯
    vol_mult = 1.2  # Volume filter multiplier for entry confirmation 📊
    adx_threshold = 20  # ADX filter for trend strength to avoid weak markets 🌊
    trail_mult = 2.0  # ATR multiplier for trailing stop to lock in profits 🔒

    def init(self):
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.slowk = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[0], self.data.High, self.data.Low, self.data.Close)
        self.slowd = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[1], self.data.High, self.data.Low, self.data.Close)
        self.sma200 = self.I(talib.SMA, self.data.Close, timeperiod=200)
        self.sma50 = self.I(talib.SMA, self.data.Close, timeperiod=50)  # Added SMA50 for shorter-term trend filter 📈
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        self.avg_vol = self.I(talib.SMA, self.data.Volume, timeperiod=20)  # Added average volume for entry filter 📊
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)  # Added ADX for market regime filter 🌊
        self.entry_bar = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.trail_sl = None  # Initialized for trailing stop management 🔒
        self.div_low = None
        self.div_bar = None
        print("🌙 Moon Dev Backtest Initialized! ✨ Initializing indicators with optimizations... 🚀")

    def next(self):
        if len(self.data) < 220:  # Enough for SMA200 + lookback
            return

        current_price = self.data.Close[-1]
        current_bar = len(self.data) - 1

        # Check if divergence signal still valid
        if self.div_bar is not None and (current_bar - self.div_bar > self.max_bars):
            self.div_bar = None
            self.div_low = None
            print("🌙 Moon Dev: Divergence signal timed out after max_bars. 🔚")

        # RSI Divergence Detection (approximation) - Tightened RSI threshold for deeper oversold conditions
        lows_older = self.data.Low[-self.lookback : -self.recent_period]
        rsi_older = self.rsi[-self.lookback : -self.recent_period]
        argmin_low_older = np.argmin(lows_older)
        low1 = lows_older[argmin_low_older]
        rsi1 = rsi_older[argmin_low_older]
        lows_recent_part = self.data.Low[-self.recent_period:]
        rsi_recent_part = self.rsi[-self.recent_period:]
        argmin_low_recent = np.argmin(lows_recent_part)
        low2 = lows_recent_part[argmin_low_recent]
        rsi2 = rsi_recent_part[argmin_low_recent]
        distance = self.recent_period - 1 - argmin_low_recent

        bull_div = (low2 < low1) and (rsi2 > rsi1) and (rsi2 < 40) and (distance <= 5)  # Tightened to RSI <40 for stronger signals 🎯
        if bull_div:
            self.div_low = low2
            self.div_bar = current_bar
            print(f"🌙 Moon Dev: Bullish RSI Divergence detected! Price low: {low2}, RSI: {rsi2} ✨ Distance: {distance} bars")

        # Stochastic Convergence - Adjusted oversold threshold slightly
        k_now = self.slowk[-1]
        d_now = self.slowd[-1]
        k_prev = self.slowk[-self.stoch_window]
        d_prev = self.slowd[-self.stoch_window]
        gap_now = abs(d_now - k_now)
        gap_prev = abs(d_prev - k_prev)
        stoch_converge = (k_now < 25) and (k_now > k_prev) and (gap_now < gap_prev) and (k_now < d_now)  # Loosened to <25 for more opportunities 🚀
        if stoch_converge:
            print(f"🚀 Moon Dev: Stochastic Convergence! %K: {k_now}, %D: {d_now}, Gap narrowed! 🌙")

        # Entry Logic - Added volume, ADX, and dual SMA filters for higher quality setups 📊
        if not self.position and self.div_bar is not None and stoch_converge and current_price > self.sma200[-1] and current_price > self.sma50[-1] and self.sma50[-1] > self.sma200[-1] and self.data.Volume[-1] > (self.vol_mult * self.avg_vol[-1]) and self.adx[-1] > self.adx_threshold:
            print("🌙 Moon Dev: All entry conditions met with filters! Attempting LONG entry... 🚀")
            # Calculate SL: below div low with reduced buffer for better RR
            div_low = self.div_low
            atr_val = self.atr[-1]
            sl_price = div_low - (atr_val * self.buffer_mult)
            entry_price = current_price
            risk_per_unit = entry_price - sl_price
            if risk_per_unit <= 0:
                print(f"🌙 Moon Dev: Invalid risk per unit: {risk_per_unit}, skipping entry. ⚠️")
                return
            size_frac = self.risk_pct * entry_price / risk_per_unit
            size = min(1.0, size_frac)
            if size > 0:
                tp_price = entry_price + self.rr * risk_per_unit
                self.buy(size=size, sl=sl_price, tp=tp_price)
                self.entry_price = entry_price
                self.sl_price = sl_price
                self.tp_price = tp_price
                self.trail_sl = sl_price  # Initialize trailing SL 🔒
                self.entry_bar = current_bar
                self.div_bar = None
                self.div_low = None
                print(f"🌙 Moon Dev: LONG ENTRY! Size: {size} (fraction), Entry: {entry_price}, SL: {sl_price}, TP: {tp_price} 🚀✨")
            else:
                print(f"🌙 Moon Dev: Calculated size {size} <=0, skipping entry. ⚠️")

        # Exit Logic - Added trailing stop updates and SMA50 exit for better risk management
        if self.position:
            bars_held = current_bar - self.entry_bar if self.entry_bar is not None else 0
            rsi_now = self.rsi[-1]
            k_cross_below_d = (self.slowk[-2] >= self.slowd[-2]) and (self.slowk[-1] < self.slowd[-1])

            # Trend filter exit: Close if below SMA50
            if current_price < self.sma50[-1]:
                self.position.close()
                print("🌙 Moon Dev: EXIT below SMA50 trend line! 📉")
                return

            # Trailing stop update for longs - Update SL to capture profits dynamically 🔒
            if self.trail_sl is not None:
                new_trail = current_price - (self.atr[-1] * self.trail_mult)
                if new_trail > self.trail_sl:
                    self.trail_sl = new_trail
                    self.position.sl = self.trail_sl  # Update the position's SL in backtesting.py
                    print(f"🌙 Moon Dev: Trailing SL updated to {self.trail_sl} (ATR-based) 🔒")

            # Time-based exit
            if bars_held > self.max_bars:
                self.position.close()
                print(f"🌙 Moon Dev: Time-based EXIT after {bars_held} bars! 😴")
                return

            # Dynamic exits - Tightened RSI exit threshold
            if k_cross_below_d:
                self.position.close()
                print("🌙 Moon Dev: Stochastic %K crossed below %D! EXIT 📉")
                return
            if rsi_now > 75:  # Tightened to >75 for holding through minor overbought 📈
                self.position.close()
                print("🌙 Moon Dev: RSI Overbought >75! EXIT 📈")
                return

            print(f"🌙 Moon Dev: Position held. Bars: {bars_held}, Price: {current_price}, RSI: {rsi_now}, %K: {self.slowk[-1]} ✨")

# Run backtest
bt = Backtest(data, DivergentConvergence, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)