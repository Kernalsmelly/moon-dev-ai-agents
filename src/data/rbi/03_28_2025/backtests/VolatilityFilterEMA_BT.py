# 🌙 Moon Dev's VolatilityFilterEMA Backtest Implementation
import pandas as pd
import talib
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

# 🌌 Data Preparation Magic
def load_data(path):
    data = pd.read_csv(
        path,
        parse_dates=['datetime'],
        index_col='datetime'
    )
    # 🧹 Cleanse column names
    data.columns = data.columns.str.strip().str.lower()
    data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
    
    # 🔮 Standardize column names
    data.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    return data

class VolatilityFilterEMA(Strategy):
    risk_percent = 0.01  # 1% risk per trade
    
    def init(self):
        # 🌀 Indicator Calculation Vortex
        self.ema50 = self.I(talib.EMA, self.data.Close, 50, name='EMA50')
        self.ema200 = self.I(talib.EMA, self.data.Close, 200, name='EMA200')
        
        # 🌪️ ATR Storm Detection System
        self.atr = self.I(talib.ATR, 
                         self.data.High, 
                         self.data.Low, 
                         self.data.Close, 
                         14, name='ATR14')
        self.atr_avg = self.I(talib.SMA, self.atr, 20, name='ATR_AVG20')

    def next(self):
        # 🌓 Current Cosmic Readings
        price = self.data.Close[-1]
        ema50 = self.ema50[-1]
        ema200 = self.ema200[-1]
        atr = self.atr[-1]
        atr_avg = self.atr_avg[-1]

        # 🌠 Moon Dev Debug Console
        if len(self.data) % 100 == 0:
            print("\n🌕 Lunar Status Report 🌕")
            print(f"│ Price: {price:.2f} │ EMA50: {ema50:.2f}")
            print(f"│ EMA200: {ema200:.2f} │ ATR: {atr:.2f}")
            print(f"╰──────────────── ATR Avg: {atr_avg:.2f} ────────────────╯")

        # 🚀 Launch Sequence (Entry Conditions)
        if not self.position:
            if crossover(self.ema50, self.ema200):
                if atr < atr_avg:
                    # 💰 Risk Management Protocol
                    risk_amount = self.equity * self.risk_percent
                    stop_loss = 2 * atr
                    
                    if stop_loss > 0:
                        position_size = risk_amount / (stop_loss * price)
                        position_size = int(round(position_size))
                        
                        if position_size > 0:
                            print("\n🚀🌙 LIFTOFF DETECTED! 🚀")
                            print(f"│ Entry Price: {price:.2f}")
                            print(f"│ Position Size: {position_size} units")
                            print(f"╰────── Stop Loss: {price - stop_loss:.2f} ──────╯")
                            self.buy(size=position_size, 
                                    sl=price - stop_loss,
                                    tag='GoldenCrossEntry')

        # 🌑 Black Hole Exit (Volatility Spike)
        else:
            if atr > atr_avg:
                print("\n🌑⚡ VOLATILITY SPIKE! EXITING! ⚡")
                print(f"╰──── Current ATR: {atr:.2f} > Avg {atr_avg:.2f} ────╯")
                self.sell()

# 🪐 Galactic Backtest Execution
if __name__ == '__main__':
    data = load_data('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')
    
    bt = Backtest(data, VolatilityFilterEMA, 
                 cash=1_000_000, commission=.002, 
                 exclusive_orders=True)
    
    # 🏁 Launch Backtest
    stats = bt.run()
    
    # 📜 Cosmic Performance Report
    print("\n🌌🌌🌌 FINAL MISSION STATISTICS 🌌🌌🌌")
    print(stats)
    print(stats._strategy)