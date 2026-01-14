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
    lookback = 20  # 🌙 Moon Dev Optimization: Increased lookback to 20 for broader historical low detection, capturing stronger potential divergences without overfitting
    recent_period = 6  # 🌙 Moon Dev Optimization: Reduced recent period to 6 for tighter, more responsive divergence detection in volatile BTC markets
    stoch_window = 10  # 🌙 Moon Dev Optimization: Extended stochastic window to 10 for smoother, less noisy convergence signals
    risk_pct = 0.02  # 🌙 Moon Dev Optimization: Increased risk per trade to 2% to amplify returns while staying within conservative risk bounds
    rr = 3.0  # 🌙 Moon Dev Optimization: Boosted RR to 3:1 to target higher reward capture in trending BTC moves
    max_bars = 40  # 🌙 Moon Dev Optimization: Extended max hold to 40 bars to allow more trend development time without excessive exposure
    atr_mult_sl = 1.8  # 🌙 Moon Dev Optimization: Adjusted ATR trailing multiplier to 1.8 for balanced profit locking – tighter than before but with room for volatility
    buffer_mult = 0.6  # 🌙 Moon Dev Optimization: Slightly tightened initial SL buffer to 0.6 ATR for better risk efficiency on divergence lows
    vol_mult = 1.2  # 🌙 Moon Dev Optimization: Introduced volume multiplier of 1.2x SMA to ensure entries on moderately elevated volume for conviction without being too restrictive
    adx_threshold = 18  # 🌙 Moon Dev Optimization: Lowered ADX threshold to 18 to allow more entries in mildly trending conditions, increasing trade frequency realistically

    def init(self):
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=14)
        self.slowk = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[0], self.data.High, self.data.Low, self.data.Close)
        self.slowd = self.I(lambda h, l, c: talib.STOCH(h, l, c, fastk_period=14, slowk_period=3, slowd_period=3)[1], self.data.High, self.data.Low, self.data.Close)
        self.sma200 = self.I(talib.SMA, self.data.Close, timeperiod=200)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        # 🌙 Moon Dev Optimization: Retained volume SMA filter, now with multiplier for nuanced high-volume confirmation
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=20)
        # 🌙 Moon Dev Optimization: Retained ADX for trend strength, with adjustable threshold for more opportunities
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        # 🌙 Moon Dev Optimization: Added EMA21 as short-term trend filter to confirm bullish bias before entry
        self.ema21 = self.I(talib.EMA, self.data.Close, timeperiod=21)
        self.entry_bar = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.div_low = None
        self.div_bar = None
        self.current_sl = None  # 🌙 Moon Dev Optimization: Track current trailing SL level
        self.partial_taken = False  # 🌙 Moon Dev Optimization: Flag for partial profit taking
        self.risk_per_unit = None  # 🌙 Moon Dev Optimization: Store risk per unit for dynamic profit targets
        print("🌙 Moon Dev Backtest Initialized! ✨ Initializing indicators... 🚀")

    def reset_trade_vars(self):
        """🌙 Moon Dev Optimization: Centralized reset function to clean up trade variables on exit for robust state management"""
        self.entry_bar = None
        self.entry_price = None
        self.sl_price = None
        self.tp_price = None
        self.div_low = None
        self.div_bar = None
        self.current_sl = None
        self.partial_taken = False
        self.risk_per_unit = None
        print("🌙 Moon Dev: Trade variables reset. Ready for next setup! 🔄")

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

        # RSI Divergence Detection (approximation)
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

        bull_div = (low2 < low1) and (rsi2 > rsi1) and (rsi2 < 40) and (distance <= 5)  # 🌙 Moon Dev Optimization: Tightened RSI oversold to <40 for deeper, higher-quality signals; extended distance to <=5 for slightly more flexible timing
        if bull_div:
            self.div_low = low2
            self.div_bar = current_bar
            print(f"🌙 Moon Dev: Bullish RSI Divergence detected! Price low: {low2}, RSI: {rsi2} ✨ Distance: {distance} bars")

        # Stochastic Convergence
        k_now = self.slowk[-1]
        d_now = self.slowd[-1]
        k_prev = self.slowk[-self.stoch_window]
        d_prev = self.slowd[-self.stoch_window]
        gap_now = abs(d_now - k_now)
        gap_prev = abs(d_prev - k_prev)
        stoch_converge = (k_now < 20) and (k_now > k_prev) and (gap_now < gap_prev) and (k_now < d_now)  # 🌙 Moon Dev Optimization: Lowered %K threshold to <20 to capture more oversold convergence opportunities in BTC's volatile swings
        if stoch_converge:
            print(f"🚀 Moon Dev: Stochastic Convergence! %K: {k_now}, %D: {d_now}, Gap narrowed! 🌙")

        # Entry Logic
        if not self.position and self.div_bar is not None and stoch_converge and current_price > self.sma200[-1] and current_price > self.ema21[-1] and self.data.Volume[-1] > self.vol_mult * self.vol_sma[-1] and self.adx[-1] > self.adx_threshold:  # 🌙 Moon Dev Optimization: Added EMA21 uptrend filter and volume multiplier for enhanced entry quality in short-term bullish regimes; lowered ADX for more trades
            print("🌙 Moon Dev: All entry conditions met! Attempting LONG entry... 🚀")
            # Calculate SL: below div low with buffer
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
                self.buy(size=size)  # 🌙 Moon Dev Optimization: Removed TP from order to enable manual partial and full profit taking
                self.entry_price = entry_price
                self.sl_price = sl_price
                self.tp_price = tp_price
                self.entry_bar = current_bar
                self.risk_per_unit = risk_per_unit  # 🌙 Moon Dev Optimization: Store for partial TP calculations
                self.partial_taken = False  # 🌙 Moon Dev Optimization: Reset partial flag on new entry
                self.current_sl = sl_price  # 🌙 Moon Dev Optimization: Initialize trailing SL at entry SL level
                self.div_bar = None
                self.div_low = None
                print(f"🌙 Moon Dev: LONG ENTRY! Size: {size} (fraction), Entry: {entry_price}, Initial SL: {sl_price}, TP: {tp_price} 🚀✨")
            else:
                print(f"🌙 Moon Dev: Calculated size {size} <=0, skipping entry. ⚠️")

        # Exit Logic
        if self.position:
            bars_held = current_bar - self.entry_bar if self.entry_bar is not None else 0
            rsi_now = self.rsi[-1]
            k_cross_below_d = (self.slowk[-2] >= self.slowd[-2]) and (self.slowk[-1] < self.slowd[-1])

            # 🌙 Moon Dev Optimization: Profit taking logic first – partial at 1:1 RR to lock gains, full at target RR to maximize upside
            if self.entry_price is not None and self.risk_per_unit is not None:
                profit = current_price - self.entry_price
                partial_rr = 1.0  # 🌙 Moon Dev Optimization: Fixed partial RR at 1:1 for early profit securing
                if profit >= partial_rr * self.risk_per_unit and not self.partial_taken and self.position.size > 0:
                    half_size = self.position.size * 0.5
                    self.sell(size=half_size)
                    self.partial_taken = True
                    print(f"🌙 Moon Dev: Partial TP at {partial_rr}:1 RR! Locked {half_size} size. 🔒")
                if profit >= self.rr * self.risk_per_unit:
                    self.position.close()
                    print(f"🌙 Moon Dev: Full TP at {self.rr}:1 RR! 📈")
                    self.reset_trade_vars()
                    return

            # 🌙 Moon Dev Optimization: Update trailing SL dynamically every bar to lock in profits as price rises
            if self.current_sl is None:
                self.current_sl = self.sl_price
            new_trail_sl = current_price - (self.atr[-1] * self.atr_mult_sl)
            self.current_sl = max(self.current_sl, new_trail_sl)

            # Time-based exit
            if bars_held > self.max_bars:
                self.position.close()
                print(f"🌙 Moon Dev: Time-based EXIT after {bars_held} bars! 😴")
                self.reset_trade_vars()
                return

            # Stochastic cross exit
            if k_cross_below_d:
                self.position.close()
                print("🌙 Moon Dev: Stochastic %K crossed below %D! EXIT 📉")
                self.reset_trade_vars()
                return

            # RSI overbought exit
            if rsi_now > 70:
                self.position.close()
                print("🌙 Moon Dev: RSI Overbought >70! EXIT 📈")
                self.reset_trade_vars()
                return

            # Trailing SL check
            if current_price < self.current_sl:
                self.position.close()
                print(f"🌙 Moon Dev: Trailing SL hit at {current_price}! 📉")
                self.reset_trade_vars()
                return

            print(f"🌙 Moon Dev: Position held. Bars: {bars_held}, Price: {current_price}, RSI: {rsi_now}, %K: {self.slowk[-1]}, Trail SL: {self.current_sl} ✨")

# Run backtest
bt = Backtest(data, DivergentConvergence, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)