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
    lookback = 25  # Increased lookback for better divergence detection 🌙 Improved: Wider window to capture more reliable lows
    recent_period = 10
    stoch_window = 8  # Slightly increased for smoother convergence check ✨
    risk_pct = 0.02  # Increased risk per trade to 2% for higher potential returns while still managed 🚀
    rr = 3  # Improved RR to 3:1 for better reward potential without excessive risk 📈
    max_bars = 20  # Extended max hold time to allow trends to develop 🌙
    atr_mult_sl = 1.5
    buffer_mult = 1.0  # Increased buffer for SL to account for volatility, reducing whipsaws ⚠️
    trail_mult = 2.0  # New: ATR multiplier for trailing stop after breakeven 🌟
    adx_threshold = 25  # New: ADX filter for trending markets only 🚀
    vol_mult = 1.2  # New: Volume must be > 1.2x SMA20 for confirmation of momentum 💥

    def init(self):
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.slowk = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[0], self.data.High, self.data.Low, self.data.Close)
        self.slowd = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[1], self.data.High, self.data.Low, self.data.Close)
        self.sma200 = self.I(talib.SMA, self.data.Close, timeperiod=200)
        self.ema50 = self.I(talib.EMA, self.data.Close, timeperiod=50)  # New: EMA50 for shorter-term trend filter 📊
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)  # New: ADX for market regime filter - only trade in trends 🌙
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=20)  # New: Volume SMA for momentum confirmation 💪
        self.entry_bar = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.trail_sl = None  # New: For trailing stop management 🚀
        self.breakeven_hit = False  # New: Flag for when to start trailing 🌟
        self.div_low = None
        self.div_bar = None
        print("🌙 Moon Dev Backtest Initialized! ✨ Initializing indicators... 🚀 Added ADX, EMA50, Vol SMA for optimization!")

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

        # RSI Divergence Detection (approximation) - Improved with wider lookback for better setups
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

        bull_div = (low2 < low1) and (rsi2 > rsi1) and (rsi2 < 50) and (distance <= 5)
        if bull_div:
            self.div_low = low2
            self.div_bar = current_bar
            print(f"🌙 Moon Dev: Bullish RSI Divergence detected! Price low: {low2}, RSI: {rsi2} ✨ Distance: {distance} bars")

        # Stochastic Convergence - Tightened oversold to <25 for stronger signals
        k_now = self.slowk[-1]
        d_now = self.slowd[-1]
        k_prev = self.slowk[-self.stoch_window]
        d_prev = self.slowd[-self.stoch_window]
        gap_now = abs(d_now - k_now)
        gap_prev = abs(d_prev - k_prev)
        stoch_converge = (k_now < 25) and (k_now > k_prev) and (gap_now < gap_prev) and (k_now < d_now)  # Improved: <25 for deeper oversold 🚀
        if stoch_converge:
            print(f"🚀 Moon Dev: Stochastic Convergence! %K: {k_now}, %D: {d_now}, Gap narrowed! 🌙")

        # New: Market Regime Filters - Only enter in uptrend and trending market
        uptrend = (current_price > self.ema50[-1]) and (self.ema50[-1] > self.sma200[-1])  # Improved trend filter with EMA50 📊
        trending = self.adx[-1] > self.adx_threshold  # New: ADX >25 to avoid choppy markets 🌙
        vol_confirm = self.data.Volume[-1] > (self.vol_sma[-1] * self.vol_mult)  # New: Volume surge for momentum 💥

        # Entry Logic - Added new filters for higher quality setups
        if not self.position and self.div_bar is not None and stoch_converge and uptrend and trending and vol_confirm:
            print("🌙 Moon Dev: All entry conditions met including filters! Attempting LONG entry... 🚀")
            # Calculate SL: below div low with increased buffer
            div_low = self.div_low
            atr_val = self.atr[-1]
            sl_price = div_low - (atr_val * self.buffer_mult)  # Wider buffer to reduce false stops ⚠️
            entry_price = current_price
            risk_per_unit = entry_price - sl_price
            if risk_per_unit <= 0:
                print(f"🌙 Moon Dev: Invalid risk per unit: {risk_per_unit}, skipping entry. ⚠️")
                return
            size_frac = self.risk_pct * entry_price / risk_per_unit  # Now 2% risk for more exposure
            size = min(1.0, size_frac)
            if size > 0:
                tp_price = entry_price + self.rr * risk_per_unit  # Higher RR=3 for better returns 📈
                self.buy(size=size)  # Buy without sl/tp - manage manually for trailing 🌟
                self.entry_price = entry_price
                self.sl_price = sl_price
                self.tp_price = tp_price
                self.trail_sl = sl_price  # Initial trail = SL
                self.entry_bar = current_bar
                self.div_bar = None
                self.div_low = None
                self.breakeven_hit = False
                print(f"🌙 Moon Dev: LONG ENTRY! Size: {size} (fraction), Entry: {entry_price}, SL: {sl_price}, TP: {tp_price} 🚀✨")
            else:
                print(f"🌙 Moon Dev: Calculated size {size} <=0, skipping entry. ⚠️")

        # Manual Exit Logic - Improved with trailing stop for better profit capture
        if self.position:
            bars_held = current_bar - self.entry_bar if self.entry_bar is not None else 0
            rsi_now = self.rsi[-1]
            k_cross_below_d = (self.slowk[-2] >= self.slowd[-2]) and (self.slowk[-1] < self.slowd[-1])
            atr_val = self.atr[-1]

            # Hit TP
            if current_price >= self.tp_price:
                self.position.close()
                print(f"🌙 Moon Dev: TP Hit! EXIT at {current_price} 📈")
                return

            # New: Trailing Stop Logic
            if current_price > self.entry_price + atr_val:  # Once in profit by 1 ATR, move to breakeven
                if not self.breakeven_hit:
                    self.trail_sl = self.entry_price  # Breakeven
                    self.breakeven_hit = True
                    print("🌙 Moon Dev: Moved to breakeven trail! 🌟")
                else:
                    new_trail = current_price - (atr_val * self.trail_mult)  # Trail by 2 ATR
                    if new_trail > self.trail_sl:
                        self.trail_sl = new_trail
                        print(f"🌙 Moon Dev: Trailing SL updated to {self.trail_sl} 🚀")

            # Hit SL or Trail
            if current_price <= self.trail_sl:
                self.position.close()
                print(f"🌙 Moon Dev: SL/Trail Hit at {current_price}! EXIT 📉")
                return

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

            print(f"🌙 Moon Dev: Position held. Bars: {bars_held}, Price: {current_price}, RSI: {rsi_now}, %K: {self.slowk[-1]}, Trail SL: {self.trail_sl} ✨")

# Run backtest
bt = Backtest(data, DivergentConvergence, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)