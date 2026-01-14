import pandas as pd
from backtesting import Backtest, Strategy
import talib
import numpy as np

# Load and clean data
path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(path)
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
data = data.set_index(pd.to_datetime(data['datetime'])).sort_index()
data = data[['Open', 'High', 'Low', 'Close', 'Volume']]

class SwiftStrength(Strategy):
    adx_threshold = 20  # 🌙 Lowered from 25 to allow more trend-confirming entries for higher trade frequency
    risk_per_trade = 0.02  # 🌙 Increased from 0.01 to 2% risk per trade to amplify returns while still managing risk
    rr_ratio = 3  # 🌙 Increased from 2 to 3:1 RR for larger profit targets on winning trades
    atr_multiplier = 2  # 🌙 Increased from 1.5 to 2 for wider initial stops to give trades more room in volatile BTC
    atr_period = 14
    weak_trend_threshold = 15  # 🌙 Lowered from 20 to exit only on very weak trends, keeping positions longer

    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        vol = self.data.Volume
        # 🌙 Changed MACD to standard 12,26,9 for more reliable signals in trending markets
        self.macd, self.macdsignal, self.macdhist = self.I(talib.MACD, close, fastperiod=12, slowperiod=26, signalperiod=9)
        self.adx = self.I(talib.ADX, high, low, close, timeperiod=14)
        self.plus_di = self.I(talib.PLUS_DI, high, low, close, timeperiod=14)
        self.minus_di = self.I(talib.MINUS_DI, high, low, close, timeperiod=14)
        # 🌙 Switched from SMA200 to EMA50 for faster trend response on 15m timeframe, enabling more timely entries
        self.ema50 = self.I(talib.EMA, close, timeperiod=50)
        self.atr = self.I(talib.ATR, high, low, close, timeperiod=self.atr_period)
        # 🌙 Added volume SMA for filter: only enter on above-average volume to catch stronger moves
        self.vol_sma = self.I(talib.SMA, vol, timeperiod=20)
        # 🌙 Added RSI for momentum confirmation: ensures alignment with trend direction
        self.rsi = self.I(talib.RSI, close, timeperiod=14)
        print("🌙 Moon Dev: Indicators initialized successfully! ✨")

    def next(self):
        current_price = self.data.Close[-1]
        current_adx = self.adx[-1]
        current_plus_di = self.plus_di[-1]
        current_minus_di = self.minus_di[-1]
        current_macd = self.macd[-1]
        current_signal = self.macdsignal[-1]
        current_hist = self.macdhist[-1]
        current_ema = self.ema50[-1]
        current_atr = self.atr[-1]
        current_vol = self.data.Volume[-1]
        current_vol_sma = self.vol_sma[-1]
        current_rsi = self.rsi[-1]

        # Emergency exit if trend weakens
        if self.position and current_adx < self.weak_trend_threshold:
            self.position.close()
            print(f"🌙 Moon Dev: Emergency exit due to weak trend (ADX < {self.weak_trend_threshold}) 📉")
            return

        # 🌙 Added trailing stop logic for better profit locking: trails by ATR multiplier from current price, ratcheting favorably
        if self.position:
            trail_distance = self.atr_multiplier * current_atr
            if self.position.is_long:
                new_sl = current_price - trail_distance
                if new_sl > self.position.sl:
                    self.position.sl = new_sl
                    print(f"🌙 Moon Dev: Trailing SL updated for long to {new_sl:.2f} 🔒")
            elif self.position.is_short:
                new_sl = current_price + trail_distance
                if new_sl < self.position.sl:
                    self.position.sl = new_sl
                    print(f"🌙 Moon Dev: Trailing SL updated for short to {new_sl:.2f} 🔒")

        # Exit on opposite MACD crossover
        if self.position.is_long and (self.macdsignal[-2] < self.macd[-2] and self.macdsignal[-1] > self.macd[-1]):
            self.position.close()
            print("🌙 Moon Dev: Exit long on MACD bearish reversal 🔄")
            return
        if self.position.is_short and (self.macd[-2] < self.macdsignal[-2] and self.macd[-1] > self.macdsignal[-1]):
            self.position.close()
            print("🌙 Moon Dev: Exit short on MACD bullish reversal 🔄")
            return

        # Entry logic only if no position
        if len(self.trades) == 0 and not self.position:
            if np.isnan(current_atr) or current_atr <= 0:
                print("🌙 Moon Dev: Invalid ATR, skipping entry 🚫")
                return

            sl_distance = self.atr_multiplier * current_atr
            risk_amount = self.equity * self.risk_per_trade
            position_size = risk_amount / sl_distance
            position_size = position_size if position_size > 0 else 0  # 🌙 Allow fractional sizes for finer risk control in crypto

            if position_size <= 0:
                print("🌙 Moon Dev: Position size too small, skipping entry 🚫")
                return

            # Long entry with tightened filters: volume surge + RSI momentum + EMA trend
            if (current_adx > self.adx_threshold and
                current_plus_di > current_minus_di and
                (self.macd[-2] < self.macdsignal[-2] and self.macd[-1] > self.macdsignal[-1]) and
                # 🌙 Removed redundant hist > 0 check as MACD cross implies positive histogram
                current_price > current_ema and
                current_vol > current_vol_sma and  # 🌙 Volume filter for higher-quality setups
                current_rsi > 50):  # 🌙 RSI filter for bullish momentum confirmation

                sl = current_price - sl_distance
                tp = current_price + (self.rr_ratio * sl_distance)
                if sl >= current_price or tp <= current_price:
                    print("🌙 Moon Dev: Invalid SL/TP for long, skipping 🚫")
                    return
                self.buy(size=position_size, limit=current_price, sl=sl, tp=tp)
                print(f"🌙 Moon Dev: Long entry at {current_price:.2f}, size {position_size}, SL {sl:.2f}, TP {tp:.2f} 🚀")

            # Short entry with tightened filters: volume surge + RSI momentum + EMA trend
            elif (current_adx > self.adx_threshold and
                  current_minus_di > current_plus_di and
                  (self.macdsignal[-2] < self.macd[-2] and self.macdsignal[-1] > self.macd[-1]) and
                  # 🌙 Removed redundant hist < 0 check as MACD cross implies negative histogram
                  current_price < current_ema and
                  current_vol > current_vol_sma and  # 🌙 Volume filter for higher-quality setups
                  current_rsi < 50):  # 🌙 RSI filter for bearish momentum confirmation

                sl = current_price + sl_distance
                tp = current_price - (self.rr_ratio * sl_distance)
                if sl <= current_price or tp >= current_price:
                    print("🌙 Moon Dev: Invalid SL/TP for short, skipping 🚫")
                    return
                self.sell(size=position_size, limit=current_price, sl=sl, tp=tp)
                print(f"🌙 Moon Dev: Short entry at {current_price:.2f}, size {position_size}, SL {sl:.2f}, TP {tp:.2f} 📉")

# Run backtest
bt = Backtest(data, SwiftStrength, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)