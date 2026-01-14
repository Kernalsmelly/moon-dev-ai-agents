# 🌙 Moon Dev's GammaReversion Backtest 🌙
import pandas as pd
import talib
from backtesting import Backtest, Strategy

# Data Preparation
def load_data(path):
    data = pd.read_csv(path)
    # Clean column names
    data.columns = data.columns.str.strip().str.lower()
    # Drop unnamed columns
    data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
    # Ensure proper case for required columns
    data.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }, inplace=True)
    data['DateTime'] = pd.to_datetime(data['datetime'])
    data.set_index('DateTime', inplace=True)
    return data

class GammaReversion(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade 🌙
    gamma_period = 20      # Swing high period for gamma levels
    funding_period = 32    # 8h funding rate SMA (15m * 32 = 8h)
    cluster_period = 50    # Liquidation cluster lookback
    
    def init(self):
        # 🌙 Gamma Levels (Swing Highs)
        self.gamma_high = self.I(talib.MAX, self.data.High, self.gamma_period, name='Gamma High')
        
        # ✨ Funding Rate Proxy (Close vs SMA)
        self.funding_sma = self.I(talib.SMA, self.data.Close, self.funding_period, name='Funding SMA')
        
        # 🚀 Liquidation Clusters (Volume-Weighted)
        typical_price = (self.data.High + self.data.Low + self.data.Close) / 3
        vwap = (typical_price * self.data.Volume).cumsum() / self.data.Volume.cumsum()
        self.liquidation_cluster = self.I(lambda: vwap.rolling(self.cluster_period).mean(), name='Liquidation Cluster')
        
    def next(self):
        current_close = self.data.Close[-1]
        current_high = self.data.High[-1]
        gamma_level = self.gamma_high[-1]
        funding_value = (current_close - self.funding_sma[-1])/self.funding_sma[-1]
        
        # 🌙 Entry Conditions
        if (current_high > gamma_level and 
            funding_value > 0.001 and 
            not self.position):
            
            # 🚀 Risk Management Calculations
            stop_loss = gamma_level * 1.02  # 2% buffer above gamma level
            take_profit = self.liquidation_cluster[-1]
            
            risk_amount = self.risk_per_trade * self.equity
            risk_per_unit = abs(stop_loss - current_close)
            position_size = int(round(risk_amount / risk_per_unit))
            
            if position_size > 0:
                print(f"🌙 SHORT ENTRY @ {current_close:.2f}")
                print(f"🚀 Size: {position_size} | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
                self.sell(size=position_size, sl=stop_loss, tp=take_profit)
        
        # ✨ Exit Condition (Stop Hit)
        elif self.position and (self.data.High[-1] >= self.position.sl):
            print(f"🌙 STOP LOSS TRIGGERED @ {self.data.High[-1]:.2f}")
            self.position.close()
            
        # ✨ Exit Condition (Take Profit Hit)
        elif self.position and (self.data.Low[-1] <= self.position.tp):
            print(f"🌙 TAKE PROFIT REACHED @ {self.data.Low[-1]:.2f}")
            self.position.close()

# Backtest Execution
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = load_data(data_path)

bt = Backtest(data, GammaReversion, cash=1_000_000, exclusive_orders=True)
stats = bt.run()
print("\n🌙✨🚀 FULL BACKTEST RESULTS 🚀✨🌙")
print(stats)
print(stats._strategy)