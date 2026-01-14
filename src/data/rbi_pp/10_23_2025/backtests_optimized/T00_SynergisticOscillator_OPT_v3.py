from backtesting import Backtest, Strategy
import talib
import pandas as pd

data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path, parse_dates=['datetime'])
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})
data = data.set_index(pd.to_datetime(data['datetime']))

class SynergisticOscillator(Strategy):
    risk_per_trade = 0.02  # 🌙 Increased to 2% for higher exposure to achieve target returns while maintaining risk control
    atr_multiplier = 2.0  # 🌙 Widened initial stop to 2x ATR for better risk-reward ratio
    partialed = False
    pos_bars = 0

    def init(self):
        self.sma = self.I(talib.SMA, self.data.Close, timeperiod=20)  # 🌙 Shortened SMA to 20 periods for faster trend response on 15m timeframe
        self.stoch_k, self.stoch_d = self.I(talib.STOCHRSI, self.data.Close, timeperiod=14, fastk_period=5, fastd_period=3)  # 🌙 Faster StochRSI with fastk=5 for quicker signal detection
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14)
        self.vol_short = self.I(talib.SMA, self.data.Volume, timeperiod=5)
        self.vol_long = self.I(talib.SMA, self.data.Volume, timeperiod=34)
        self.adx = self.I(talib.ADX, self.data.High, self.data.Low, self.data.Close, timeperiod=14)  # 🌙 Added ADX for trend strength filter to avoid choppy markets
        self.partialed = False
        self.pos_bars = 0
        print("🌙 SynergisticOscillator initialized! ✨")

    def next(self):
        if self.position:
            if self.pos_bars == 0:
                self.pos_bars = 1
            else:
                self.pos_bars += 1

        close = self.data.Close
        high = self.data.High
        low = self.data.Low
        vol_osc = self.vol_short - self.vol_long
        k = self.stoch_k
        d = self.stoch_d
        sma = self.sma
        atr_val = self.atr
        adx_val = self.adx

        # Exit conditions first
        if self.position:
            if self.pos_bars >= 20:  # 🌙 Extended time-based exit to 20 bars to allow more room for profitable trades
                self.position.close()
                print("🌙 Time-based exit after 20 bars! ⏰")
                self.partialed = False
                self.pos_bars = 0
                return

            # Trailing stop logic for better profit locking
            # 🌙 Implemented ATR-based trailing stop to protect gains dynamically
            trail_mult = 2.0
            if self.position.is_long:
                new_sl = close[-1] - trail_mult * atr_val[-1]
                if new_sl > self.position.sl:
                    self.position.sl = new_sl
                    print(f"🌙 Trailing SL updated to {new_sl:.2f} for long 📈")
            elif self.position.is_short:
                new_sl = close[-1] + trail_mult * atr_val[-1]
                if new_sl < self.position.sl:
                    self.position.sl = new_sl
                    print(f"🌙 Trailing SL updated to {new_sl:.2f} for short 📉")

            if self.position.is_long:
                # SMA exit
                if close[-1] < sma[-1]:
                    self.position.close()
                    print("🌙 SMA crossover exit for long! 📉")
                    self.partialed = False
                    self.pos_bars = 0
                    return
                # Opposite crossover exit
                if k[-1] < d[-1] and k[-2] >= d[-2]:
                    self.position.close()
                    print("🌙 Opposite StochRSI crossover exit for long! 🔄")
                    self.partialed = False
                    self.pos_bars = 0
                    return
                # Partial profit based on ATR profit target
                # 🌙 Changed partial to 1.5x ATR profit for more reliable scaling out
                if not self.partialed:
                    profit_dist = close[-1] - self.position.price
                    if profit_dist >= 1.5 * atr_val[-1]:
                        size_to_close = self.position.size * 0.5  # 🌙 Use fractional sizing for precision
                        if size_to_close > 0:
                            self.sell(size=size_to_close)
                            self.partialed = True
                            print("✨ Partial profit taken for long at 1.5x ATR! 💰")
                        return

            elif self.position.is_short:
                # SMA exit
                if close[-1] > sma[-1]:
                    self.position.close()
                    print("🌙 SMA crossover exit for short! 📈")
                    self.partialed = False
                    self.pos_bars = 0
                    return
                # Opposite crossover exit
                if k[-1] > d[-1] and k[-2] <= d[-2]:
                    self.position.close()
                    print("🌙 Opposite StochRSI crossover exit for short! 🔄")
                    self.partialed = False
                    self.pos_bars = 0
                    return
                # Partial profit based on ATR profit target
                if not self.partialed:
                    profit_dist = self.position.price - close[-1]
                    if profit_dist >= 1.5 * atr_val[-1]:
                        size_to_close = abs(self.position.size) * 0.5  # 🌙 Use fractional sizing for precision
                        if size_to_close > 0:
                            self.buy(size=size_to_close)
                            self.partialed = True
                            print("✨ Partial profit taken for short at 1.5x ATR! 💰")
                        return

        # Entry conditions only if no position
        if not self.position:
            vol_ok = self.vol_short[-1] > self.vol_long[-1]  # 🌙 Unified and tightened volume filter: require increasing volume for both directions for momentum confirmation
            trend_strength = adx_val[-1] > 25  # 🌙 Added ADX filter to only trade in trending markets, avoiding ranging conditions
            # Long entry
            cross_up = k[-1] > d[-1] and k[-2] <= d[-2]
            oversold = k[-1] < 30 and d[-1] < 30  # 🌙 Relaxed oversold to 30 for more quality setups without too many false signals
            uptrend = close[-1] > sma[-1]
            if cross_up and oversold and uptrend and vol_ok and trend_strength:
                entry_price = close[-1]
                stop_dist = self.atr_multiplier * atr_val[-1]
                risk_amount = self.risk_per_trade * self.equity
                size = risk_amount / stop_dist  # 🌙 Use exact float sizing for precise risk management
                if size > 0:
                    self.buy(size=size, sl=entry_price - stop_dist)
                    print(f"🚀 Long entry at {entry_price:.2f}, size {size}, SL {entry_price - stop_dist:.2f}, ATR {atr_val[-1]:.2f} 🌙")
                    self.partialed = False
                    self.pos_bars = 0

            # Short entry
            cross_down = k[-1] < d[-1] and k[-2] >= d[-2]
            overbought = k[-1] > 70 and d[-1] > 70  # 🌙 Relaxed overbought to 70 for more quality setups
            downtrend = close[-1] < sma[-1]
            if cross_down and overbought and downtrend and vol_ok and trend_strength:
                entry_price = close[-1]
                stop_dist = self.atr_multiplier * atr_val[-1]
                risk_amount = self.risk_per_trade * self.equity
                size = risk_amount / stop_dist  # 🌙 Use exact float sizing for precise risk management
                if size > 0:
                    self.sell(size=size, sl=entry_price + stop_dist)
                    print(f"📉 Short entry at {entry_price:.2f}, size {size}, SL {entry_price + stop_dist:.2f}, ATR {atr_val[-1]:.2f} 🌙")
                    self.partialed = False
                    self.pos_bars = 0

bt = Backtest(data, SynergisticOscillator, cash=1000000, commission=0.002, exclusive_orders=True)
stats = bt.run()
print(stats)