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
    adx_threshold = 30  # 🌙 Moon Dev: Tightened ADX threshold from 25 to 30 for stronger trend confirmation, reducing false entries in weaker markets
    risk_per_trade = 0.02  # 🌙 Moon Dev: Increased risk per trade from 0.01 to 0.02 to allow larger positions and higher potential returns while still managing risk
    rr_ratio = 3  # 🌙 Moon Dev: Increased RR ratio from 2 to 3 for better reward potential on winning trades, aiming to boost overall returns
    atr_multiplier = 1.2  # 🌙 Moon Dev: Reduced ATR multiplier from 1.5 to 1.2 for tighter stop losses, improving win rate by allowing less room for adverse moves
    atr_period = 14
    weak_trend_threshold = 18  # 🌙 Moon Dev: Adjusted weak trend threshold from 20 to 18 to hold positions a bit longer in mildly weakening trends, reducing premature exits

    def init(self):
        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        vol = self.data.Volume
        # 🌙 Moon Dev: Changed MACD params to standard 12,26,9 from 8,17,9 for smoother signals with less noise, improving quality of crossovers
        self.macd, self.macdsignal, self.macdhist = self.I(talib.MACD, close, fastperiod=12, slowperiod=26, signalperiod=9)
        self.adx = self.I(talib.ADX, high, low, close, timeperiod=14)
        self.plus_di = self.I(talib.PLUS_DI, high, low, close, timeperiod=14)
        self.minus_di = self.I(talib.MINUS_DI, high, low, close, timeperiod=14)
        self.sma200 = self.I(talib.SMA, close, timeperiod=200)
        # 🌙 Moon Dev: Added RSI indicator for momentum filter to avoid entries in overextended conditions
        self.rsi = self.I(talib.RSI, close, timeperiod=14)
        # 🌙 Moon Dev: Added volume SMA for filter to ensure entries on above-average volume, confirming conviction in moves
        self.avg_volume = self.I(talib.SMA, vol, timeperiod=20)
        self.atr = self.I(talib.ATR, high, low, close, timeperiod=self.atr_period)
        print("🌙 Moon Dev: Indicators initialized successfully! ✨")

    def next(self):
        current_price = self.data.Close[-1]
        current_adx = self.adx[-1]
        current_plus_di = self.plus_di[-1]
        current_minus_di = self.minus_di[-1]
        current_macd = self.macd[-1]
        current_signal = self.macdsignal[-1]
        current_hist = self.macdhist[-1]
        current_sma = self.sma200[-1]
        current_rsi = self.rsi[-1]
        current_volume = self.data.Volume[-1]
        current_avg_vol = self.avg_volume[-1]
        current_atr = self.atr[-1]

        # 🌙 Moon Dev: Emergency exit if trend weakens - kept logic but with adjusted threshold
        if self.position and current_adx < self.weak_trend_threshold:
            self.position.close()
            print(f"🌙 Moon Dev: Emergency exit due to weak trend (ADX < {self.weak_trend_threshold}) 📉")
            return

        # Exit on opposite MACD crossover - unchanged for consistency
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
            # 🌙 Moon Dev: Added volume filter to skip low-conviction entries
            if np.isnan(current_avg_vol) or current_volume < current_avg_vol * 1.1:
                print("🌙 Moon Dev: Volume too low, skipping entry 🚫")
                return

            if np.isnan(current_atr) or current_atr <= 0:
                print("🌙 Moon Dev: Invalid ATR, skipping entry 🚫")
                return

            sl_distance = self.atr_multiplier * current_atr
            risk_amount = self.equity * self.risk_per_trade
            # 🌙 Moon Dev: Changed to float position sizing for more precise risk control, especially in volatile crypto; capped exposure at 10% of equity to manage risk
            position_size = risk_amount / sl_distance
            max_exposure = self.equity * 0.1
            if position_size * current_price > max_exposure:
                position_size = max_exposure / current_price
            if position_size <= 0:
                print("🌙 Moon Dev: Position size too small, skipping entry 🚫")
                return

            # Long entry with added RSI filter (>50 for bullish momentum)
            if (current_adx > self.adx_threshold and
                current_plus_di > current_minus_di and
                (self.macd[-2] < self.macdsignal[-2] and self.macd[-1] > self.macdsignal[-1]) and
                current_hist > 0 and
                current_price > current_sma and
                current_rsi > 50):  # 🌙 Moon Dev: Added RSI >50 filter to ensure sufficient bullish momentum, filtering out weak setups

                sl = current_price - sl_distance
                tp = current_price + (self.rr_ratio * sl_distance)
                if sl >= current_price or tp <= current_price:
                    print("🌙 Moon Dev: Invalid SL/TP for long, skipping 🚫")
                    return
                self.buy(size=position_size, limit=current_price, sl=sl, tp=tp)
                print(f"🌙 Moon Dev: Long entry at {current_price:.2f}, size {position_size:.4f}, SL {sl:.2f}, TP {tp:.2f} 🚀")

            # Short entry with added RSI filter (<50 for bearish momentum)
            elif (current_adx > self.adx_threshold and
                  current_minus_di > current_plus_di and
                  (self.macdsignal[-2] < self.macd[-2] and self.macdsignal[-1] > self.macd[-1]) and
                  current_hist < 0 and
                  current_price < current_sma and
                  current_rsi < 50):  # 🌙 Moon Dev: Added RSI <50 filter to ensure sufficient bearish momentum, filtering out weak setups

                sl = current_price + sl_distance
                tp = current_price - (self.rr_ratio * sl_distance)
                if sl <= current_price or tp >= current_price:
                    print("🌙 Moon Dev: Invalid SL/TP for short, skipping 🚫")
                    return
                self.sell(size=position_size, limit=current_price, sl=sl, tp=tp)
                print(f"🌙 Moon Dev: Short entry at {current_price:.2f}, size {position_size:.4f}, SL {sl:.2f}, TP {tp:.2f} 📉")

# Run backtest
bt = Backtest(data, SwiftStrength, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)