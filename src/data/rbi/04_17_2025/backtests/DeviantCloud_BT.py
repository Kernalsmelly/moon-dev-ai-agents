# -*- coding: utf-8 -*-
from backtesting import Backtest, Strategy
import pandas as pd
import pandas_ta as ta

class DeviantCloud(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade
    
    def init(self):
        # 🌙 MOON DEV DATA PREPARATION 🌙
        # Ichimoku Cloud Calculation
        high = self.data.High
        low = self.data.Low
        
        # Calculate Ichimoku components using pandas_ta
        ichimoku = ta.ichimoku(high, low, tenkan=9, kijun=26, senkou=52)
        self.tenkan = self.I(ichimoku['ITS_9'], name='Tenkan')
        self.kijun = self.I(ichimoku['IKS_26'], name='Kijun')
        self.senkou_a = self.I(ichimoku['ISA_9'], name='Senkou A')
        self.senkou_b = self.I(ichimoku['ISB_26'], name='Senkou B')
        
        # Funding Rate Analysis
        funding_series = self.data.df['funding_rate']
        self.funding_mean = self.I(lambda x: funding_series.rolling(2880).mean(), name='Fund30MA')  # 30-day (2880*15m)
        self.funding_std = self.I(lambda x: funding_series.rolling(2880).std(), name='Fund30Std')
        self.funding_upper = self.I(lambda x: self.funding_mean + 2*self.funding_std, name='FundUpper')
        
    def next(self):
        price = self.data.Close[-1]
        
        # 🌙✨ ENTRY LOGIC ✨🌙
        if not self.position:
            # Funding rate extreme condition
            fund_condition = (self.data.df['funding_rate'][-1] > self.funding_upper[-1])
            
            # Ichimoku cloud condition
            cloud_top = max(self.senkou_a[-1], self.senkou_b[-1])
            cloud_condition = (price < cloud_top)
            
            if fund_condition and cloud_condition:
                # 🚀 RISK MANAGEMENT CALCULATIONS 🚀
                stop_loss = cloud_top
                risk_amount = self.equity * self.risk_per_trade
                risk_per_share = abs(stop_loss - price)
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    self.sell(size=position_size, sl=stop_loss, tag="moon_short")
                    print("🌙✨ MOON DEV SHORT ACTIVATED ✨🌙")
                    print(f"| Entry: {price:.2f} | SL: {stop_loss:.2f}")
                    print(f"| Size: {position_size} | Risk: {self.risk_per_trade*100}% Equity")
        
        # 🌑 EXIT LOGIC 🌑
        else:
            # Mean reversion exit condition
            if self.data.df['funding_rate'][-1] <= self.funding_mean[-1]:
                self.position.close()
                print("🚀🌑 MOON DEV EXIT SIGNAL 🌑🚀")
                print(f"| Exit: {price:.2f} | PnL: {self.position.pl:.2f}")

# 🌙 DATA PREPROCESSING FOR MOON DEV SYSTEMS 🌙
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean columns
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# Ensure proper column mapping
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}).dropna()

# Set index to datetime
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

# 🌙 LAUNCH MOON DEV BACKTEST 🌙
bt = Backtest(data, DeviantCloud, cash=1_000_000, exclusive_orders=True)
stats = bt.run()
print(stats)
print(stats._strategy)