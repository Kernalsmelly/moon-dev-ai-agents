# 🌙 MOON DEV BACKTESTING SCRIPT FOR VOLATILITY RATIO REVERSION STRATEGY

# Import necessary libraries
import pandas as pd
import talib
from backtesting import Backtest, Strategy

class VolatilityRatioReversion(Strategy):
    # Strategy parameters
    upper_threshold = 1.5    # 🌕 Entry threshold 
    lower_threshold = 0.0    # 🌑 Exit threshold
    risk_percent = 0.01      # 1% risk per trade
    stop_loss_pct = 0.01     # 1% stop loss
    take_profit_pct = 0.02   # 2% take profit
    
    def init(self):
        # 🌙 Calculate indicators using TA-Lib with proper self.I() wrapper
        self.sma20 = self.I(talib.SMA, self.data.Close, timeperiod=20, name='SMA20')
        self.atr3 = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=3, name='ATR3')
        
        # 🌗 Calculate mean return ratio: (Close - SMA20)/ATR3
        self.mean_return_ratio = self.I(lambda close, sma, atr: (close - sma)/atr,
               self.data.Close, self.sma20, self.atr3,
               name='MeanReturnRatio')

    def next(self):
        # 🌟 Current strategy values
        current_ratio = self.mean_return_ratio[-1]
        equity = self.equity
        
        # 🌙✨ Entry logic
        if not self.position and current_ratio > self.upper_threshold:
            entry_price = self.data.Close[-1]
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            take_profit = entry_price * (1 + self.take_profit_pct)
            
            # 🚀 Position sizing calculation
            risk_amount = equity * self.risk_percent
            risk_per_unit = abs(entry_price - stop_loss)
            position_size = int(round(risk_amount / risk_per_unit))
            
            if position_size > 0:
                self.buy(size=position_size, sl=stop_loss, tp=take_profit)
                print(f"🌙✨ MOON DEV LONG SIGNAL! Ratio: {current_ratio:.2f} | Size: {position_size} | Entry: {entry_price:.2f} ✨🚀")

        # 🌑🛑 Exit logic
        elif self.position and current_ratio < self.lower_threshold:
            self.position.close()
            print(f"🌙🛑 MOON DEV EXIT SIGNAL! Ratio: {current_ratio:.2f} | Exit: {self.data.Close[-1]:.2f} 🛑🔻")

# 🌙 DATA PREPARATION
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

# 🧹 Clean and prepare data
try:
    data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')
    data.columns = data.columns.str.strip().str.lower()
    data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
    data = data.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    })
    
    # 🌙✨ RUN BACKTEST
    bt = Backtest(data, VolatilityRatioReversion, cash=1_000_000, commission=.002)
    stats = bt.run()

    # 🌟 PRINT FULL RESULTS
    print("\n🌕🌖🌗🌘🌑 MOON DEV BACKTEST RESULTS 🌑🌘🌗🌖🌕")
    print(stats)
    print("\n🌙 STRATEGY METRICS DETAILS:")
    print(stats._strategy)

except FileNotFoundError:
    print("🌙🛑 MOON DEV ERROR: Data file not found! Please check the path 🛑")
except Exception as e:
    print(f"🌙🛑 MOON DEV ERROR: {str(e)} 🛑")