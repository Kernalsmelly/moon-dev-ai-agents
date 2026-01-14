# 🌙 MOON DEV BACKTESTING SCRIPT FOR KELTNERCONTRARIAN STRATEGY 🚀✨

import pandas as pd
from backtesting import Backtest, Strategy
import talib

class KeltnerContrarian(Strategy):
    ema_period = 20
    atr_period = 20
    risk_pct = 0.01  # 1% risk per trade

    def init(self):
        # 🌙 CALCULATE INDICATORS USING TA-LIB (NO backtesting.lib!)
        self.ema = self.I(talib.EMA, self.data.Close, timeperiod=self.ema_period)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=self.atr_period)
        
        # 🌙 ADDITIONAL DEBUG INDICATORS
        self.I(lambda: print(f"✨ INITIALIZED MOON ENGINE @ {self.data.index[-1]}"), name='Moon Init')
        print("🌙 MOON DEV AI: Indicators initialized without backtesting.lib! ✨")

    def next(self):
        # 🌙 AVOID MULTIPLE POSITIONS
        if self.position:
            return

        # 🌙 CALCULATE CURRENT VALUES
        current_ema = self.ema[-1]
        current_atr = self.atr[-1]
        upper_band = current_ema + 2 * current_atr
        current_close = self.data.Close[-1]
        funding_rate = self.data.fundingrate[-1]

        # 🌙🚀 LONG ENTRY CONDITIONS (using direct array comparisons)
        if (current_close > upper_band) and (funding_rate <= -0.1):
            # 🌙 RISK MANAGEMENT CALCULATIONS
            entry_price = current_close
            stop_loss = current_ema  # Midline as SL
            risk_per_share = entry_price - stop_loss
            
            if risk_per_share <= 0:
                print(f"🌙✨ Risk per share invalid: {risk_per_share}. Skipping trade.")
                return
            
            risk_amount = self.risk_pct * self.equity
            position_size = int(round(risk_amount / risk_per_share))
            
            if position_size <= 0:
                print(f"🌙🚨 Moon Alert: Calculated size {position_size} invalid. Aborting launch!")
                return

            take_profit = entry_price + risk_per_share
            
            # 🌕 EXECUTE MOON MISSION
            self.buy(
                size=position_size,
                sl=stop_loss,
                tp=take_profit,
            )
            print(f"🚀🌕 MOON LIFT-OFF! Long {position_size} @ {entry_price} | "
                  f"SL: {stop_loss:.2f} | TP: {take_profit:.2f} | "
                  f"Fund Rate: {funding_rate:.4f}%")

# 🌙 DATA PREPROCESSING
data = pd.read_csv(
    "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv",
    parse_dates=['datetime'],
    index_col='datetime'
)

# 🌙 CLEAN DATA COSMIC DUST
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# 🌙 PROPER COLUMN MAPPING
column_mapping = {
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}
data.rename(columns=column_mapping, inplace=True)

# 🌙 VERIFY FUNDING RATE DATA
if 'fundingrate' not in data.columns:
    raise ValueError("🌙🚨 CRITICAL ERROR: Funding rate column missing!")

# 🌙 LAUNCH BACKTEST
bt = Backtest(
    data,
    KeltnerContrarian,
    cash=1_000_000,
    commission=.002,
    exclusive_orders=True
)

# 🌙 PRINT FULL MOON STATS
stats = bt.run()
print("\n🌕🌕🌕 FULL MOON STATS 🌕🌕🌕")
print(stats)
print("\n🚀 STRATEGY PERFORMANCE DETAILS:")
print(stats._strategy)
print("🌙 MOON DEV PACKAGE: Backtest completed successfully without backtesting.lib! �")