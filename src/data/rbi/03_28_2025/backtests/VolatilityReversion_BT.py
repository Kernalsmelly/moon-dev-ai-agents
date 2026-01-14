import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Data preprocessing
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')

# Clean and format columns
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class VolatilityReversion(Strategy):
    risk_pct = 0.01
    time_exit_bars = 480  # 5 days in 15m intervals
    
    def init(self):
        # Core indicators 🌙
        self.sma = self.I(talib.SMA, self.data.Close, 20)
        self.std = self.I(talib.STDDEV, self.data.Close, 20)
        self.upper_band = self.I(lambda sma, std: sma + 2*std, self.sma, self.std)
        self.lower_band = self.I(lambda sma, std: sma - 2*std, self.sma, self.std)
        
        # VIX structure indicators ✨
        self.vix_front = self.data['vix_front']
        self.vix_next = self.data['vix_next']

    def next(self):
        current_close = self.data.Close[-1]
        vix_backwardation = self.vix_front[-1] > self.vix_next[-1]
        vix_contango = self.vix_front[-1] < self.vix_next[-1]

        # Entry logic 🚀
        if not self.position:
            if vix_backwardation:
                risk_amount = self.equity * self.risk_pct
                current_std = self.std[-1]
                
                if current_close > self.upper_band[-1]:
                    # Short setup 🌑
                    stop_loss = current_close + 3*current_std
                    position_size = int(round(risk_amount / (3*current_std)))
                    if position_size > 0:
                        self.sell(size=position_size, sl=stop_loss)
                        print(f"🌙 MOON DEV SHORT SIGNAL! Sold {position_size} units at {current_close} ✨")
                        self.entry_bar = len(self.data)

                elif current_close < self.lower_band[-1]:
                    # Long setup 🌕
                    stop_loss = current_close - 3*current_std
                    position_size = int(round(risk_amount / (3*current_std)))
                    if position_size > 0:
                        self.buy(size=position_size, sl=stop_loss)
                        print(f"🚀 MOON DEV LONG SIGNAL! Bought {position_size} units at {current_close} 🌕")
                        self.entry_bar = len(self.data)

        # Exit logic ⏳
        else:
            exit_condition = False
            current_sma = self.sma[-1]
            exit_threshold = 0.5 * self.std[-1]
            
            # Price reversion exit ✂️
            if self.position.is_long and current_close >= (current_sma + exit_threshold):
                exit_condition = True
                print(f"✨ PRICE REVERSION EXIT at {current_close}")
                
            elif self.position.is_short and current_close <= (current_sma - exit_threshold):
                exit_condition = True
                print(f"✨ PRICE REVERSION EXIT at {current_close}")

            # VIX contango exit 🌊
            if vix_contango:
                exit_condition = True
                print(f"🌊 VIX CONTANGO EXIT at {current_close}")

            # Time-based exit ⏰
            if (len(self.data) - self.entry_bar) >= self.time_exit_bars:
                exit_condition = True
                print(f"⏳ TIME-BASED EXIT at {current_close}")

            if exit_condition:
                self.position.close()
                print("🌙🌙🌙 MOON DEV TRADE CLOSED 🌙🌙🌙")

# Run backtest 💻
bt = Backtest(data, VolatilityReversion, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)