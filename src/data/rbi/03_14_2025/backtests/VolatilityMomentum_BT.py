# 🌙 MOON DEV BACKTESTING SCRIPT 🌙
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class VolatilityMomentum(Strategy):
    # 🌌 STRATEGY PARAMETERS 🌌
    bb_period = 20
    atr_period = 14
    rsi_period = 50
    risk_percent = 0.01
    sl_multiplier = 1.5

    def init(self):
        # 🚀 INDICATOR CALCULATIONS 🚀
        # Bollinger Bands (20-period, 2 std)
        self.upper_band, self.middle_band, self.lower_band = self.I(
            talib.BBANDS, self.data.Close, 
            timeperiod=self.bb_period, nbdevup=2, nbdevdn=2,
            name=['UPPER', 'MIDDLE', 'LOWER']
        )
        
        # ATR (14-period)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close,
                         timeperiod=self.atr_period, name='ATR')
        
        # RSI (50-period)
        self.rsi = self.I(talib.RSI, self.data.Close,
                         timeperiod=self.rsi_period, name='RSI')

    def next(self):
        # 🌙 MOON DEV DEBUG TRACKING 🌙
        current_close = self.data.Close[-1]
        volatility_spread = self.upper_band[-1] - self.lower_band[-1]
        atr_value = self.atr[-1]
        rsi_value = self.rsi[-1]
        prev_rsi = self.rsi[-2] if len(self.rsi) > 1 else 50

        # 🚀 LONG ENTRY: Volatility Breakout 🚀
        if not self.position and volatility_spread > atr_value:
            stop_loss_distance = self.sl_multiplier * atr_value
            risk_amount = self.equity * self.risk_percent
            position_size = int(round(risk_amount / stop_loss_distance))
            
            if position_size > 0:
                sl_price = current_close - stop_loss_distance
                self.buy(size=position_size, sl=sl_price)
                print(f"\n🌕 MOON DEV LONG SIGNAL 🌕 | "
                      f"Price: {current_close:.2f} | "
                      f"Size: {position_size} | "
                      f"SL: {sl_price:.2f} | "
                      f"ATR: {atr_value:.2f} | "
                      f"Vol Spread: {volatility_spread:.2f}")

        # ✨ EXIT: RSI Momentum Loss ✨
        if self.position and (rsi_value < 50 and prev_rsi >= 50):
            self.position.close()
            print(f"\n🌑 MOON DEV EXIT SIGNAL 🌑 | "
                  f"Price: {current_close:.2f} | "
                  f"RSI: {rsi_value:.2f}")

# 🪐 DATA PREPARATION 🪐
data = pd.read_csv(
    '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
    parse_dates=['datetime'],
    index_col='datetime'
)

# 🧹 DATA CLEANING 🧹
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# 🚀 LAUNCH BACKTEST 🚀
bt = Backtest(data, VolatilityMomentum, cash=1_000_000)
stats = bt.run()

# 🌟 PRINT MOON DEV RESULTS 🌟
print("\n" + "="*50)
print("🌙 MOON DEV BACKTEST RESULTS 🌙")
print("="*50)
print(stats)
print("\n✨ STRATEGY PERFORMANCE DETAILS ✨")
print(stats._strategy)