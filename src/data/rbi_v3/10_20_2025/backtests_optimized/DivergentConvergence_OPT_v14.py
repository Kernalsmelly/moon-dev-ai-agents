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
    lookback = 15  # Tightened lookback for more recent divergences to catch fresher setups 🌙
    recent_period = 5  # Reduced recent_period for tighter divergence detection ✨
    stoch_window = 3  # Shortened stoch_window for faster convergence signals 🚀
    risk_pct = 0.015  # Increased risk per trade to 1.5% for higher exposure while managing risk 📈
    rr = 2.5  # Improved RR to 2.5:1 for better reward potential without excessive hold time 💫
    max_bars = 20  # Extended max_bars slightly to allow trending moves to develop 🌌
    atr_mult_sl = 1.2  # Tightened ATR multiplier for SL to reduce risk exposure 🔒
    buffer_mult = 0.3  # Reduced buffer for more precise SL placement near divergence low ⚡

    def init(self):
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.slowk = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[0], self.data.High, self.data.Low, self.data.Close)
        self.slowd = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[1], self.data.High, self.data.Low, self.data.Close)
        self.sma200 = self.I(talib.SMA, self.data.Close, timeperiod=200)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        # Added volume SMA for filter: only enter on above-average volume to avoid low-conviction signals 🌙
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=20)
        # Added ADX for trend strength filter: require ADX > 20 to avoid choppy markets 📊
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        # Added EMA 50 for intermediate trend confirmation alongside SMA200 🚀
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=50)
        self.entry_bar = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.div_low = None
        self.div_bar = None
        self.trail_active = False  # For trailing stop activation 🔄
        self.trail_sl = None  # Dynamic trailing SL price 💨
        print("🌙 Moon Dev Backtest Initialized! ✨ Initializing indicators... 🚀")

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

        # RSI Divergence Detection (approximation) - tightened RSI threshold to <40 for stronger oversold bias 📉
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

        bull_div = (low2 < low1) and (rsi2 > rsi1) and (rsi2 < 40) and (distance <= 5)  # Stricter RSI <40 for better quality ✨
        if bull_div:
            self.div_low = low2
            self.div_bar = current_bar
            print(f"🌙 Moon Dev: Bullish RSI Divergence detected! Price low: {low2}, RSI: {rsi2} ✨ Distance: {distance} bars")

        # Stochastic Convergence - adjusted threshold to <25 for deeper oversold 🚀
        k_now = self.slowk[-1]
        d_now = self.slowd[-1]
        k_prev = self.slowk[-self.stoch_window]
        d_prev = self.slowd[-self.stoch_window]
        gap_now = abs(d_now - k_now)
        gap_prev = abs(d_prev - k_prev)
        stoch_converge = (k_now < 25) and (k_now > k_prev) and (gap_now < gap_prev) and (k_now < d_now)  # Tighter oversold at 25 📊
        if stoch_converge:
            print(f"🚀 Moon Dev: Stochastic Convergence! %K: {k_now}, %D: {d_now}, Gap narrowed! 🌙")

        # Entry Logic - added volume, ADX, and EMA50 filters for higher quality setups 🌌
        if not self.position and self.div_bar is not None and stoch_converge and current_price > self.sma200[-1] and current_price > self.ema50[-1] and self.data.Volume[-1] > self.vol_sma[-1] and self.adx[-1] > 20:
            print("🌙 Moon Dev: All entry conditions met! Attempting LONG entry... 🚀")
            # Calculate SL: below div low with buffer, using tightened ATR mult
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
                self.entry_bar = current_bar
                self.trail_active = False
                self.trail_sl = None
                self.div_bar = None
                self.div_low = None
                print(f"🌙 Moon Dev: LONG ENTRY! Size: {size} (fraction), Entry: {entry_price}, SL: {sl_price}, TP: {tp_price} 🚀✨")
            else:
                print(f"🌙 Moon Dev: Calculated size {size} <=0, skipping entry. ⚠️")

        # Exit Logic
        if self.position:
            bars_held = current_bar - self.entry_bar if self.entry_bar is not None else 0
            rsi_now = self.rsi[-1]
            k_cross_below_d = (self.slowk[-2] >= self.slowd[-2]) and (self.slowk[-1] < self.slowd[-1])

            # Time-based exit
            if bars_held > self.max_bars:
                self.position.close()
                print(f"🌙 Moon Dev: Time-based EXIT after {bars_held} bars! 😴")
                return

            # Dynamic exits
            if k_cross_below_d:
                self.position.close()
                print("🌙 Moon Dev: Stochastic %K crossed below %D! EXIT 📉")
                return
            if rsi_now > 70:
                self.position.close()
                print("🌙 Moon Dev: RSI Overbought >70! EXIT 📈")
                return

            # Trailing Stop: Activate after 1:1 RR profit, then trail with ATR * 1.5 below current price for profit locking 🔒
            risk_per_unit = self.entry_price - self.sl_price if self.sl_price else 0
            if not self.trail_active and risk_per_unit > 0 and current_price >= self.entry_price + risk_per_unit:  # Activate at 1:1
                self.trail_active = True
                self.trail_sl = current_price - (self.atr[-1] * 1.5)
                self.position.sl = self.trail_sl
                print(f"🌙 Moon Dev: Trailing stop activated at {self.trail_sl}! 🚀")
            elif self.trail_active:
                new_trail = current_price - (self.atr[-1] * 1.5)
                if new_trail > self.trail_sl:
                    self.trail_sl = new_trail
                    self.position.sl = self.trail_sl
                    print(f"🌙 Moon Dev: Trailing SL updated to {self.trail_sl} ✨")

            print(f"🌙 Moon Dev: Position held. Bars: {bars_held}, Price: {current_price}, RSI: {rsi_now}, %K: {self.slowk[-1]} ✨")

# Run backtest
bt = Backtest(data, DivergentConvergence, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)