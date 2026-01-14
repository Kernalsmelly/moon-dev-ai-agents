# -*- coding: utf-8 -*-
import pandas as pd
import talib
import numpy as np
from backtesting import Backtest, Strategy

# 🌙 MOON DEV DATA PREPARATION 🌙
data = pd.read_csv(
    '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
    parse_dates=['datetime'],
    index_col='datetime'
)
# Cleanse and prepare cosmic data
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class CorrelationReversal(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade 🌙
    
    def init(self):
        # ✨ COSMIC INDICATORS ✨
        def calculate_vix(high, low, close, timeperiod):
            atr = talib.ATR(high, low, close, timeperiod)
            return atr / close
        
        # Moon-powered volatility indicator
        self.vix = self.I(calculate_vix, 
                         self.data.High, 
                         self.data.Low, 
                         self.data.Close, 
                         14, 
                         name='VIX')
        
        # 🌌 Galactic correlation matrix
        self.correlation = self.I(talib.CORREL,
                                self.vix,
                                self.data.Close,
                                20,
                                name='CORREL')
        
        # 🌕 Lunar high detection
        self.daily_high = self.I(talib.MAX,
                               self.data.High,
                               20,
                               name='Daily High')
        
        # 🌑 Dark side low prediction (look-ahead for demonstration)
        def next_day_extreme(series, timeperiod, func):
            values = func(series, timeperiod)
            return np.roll(values, -timeperiod)
        
        self.next_day_low = self.I(next_day_extreme,
                                 self.data.Low,
                                 20,
                                 talib.MIN,
                                 name='Next Day Low')
        
        self.next_day_high = self.I(next_day_extreme,
                                  self.data.High,
                                  20,
                                  talib.MAX,
                                  name='Next Day High')

    def next(self):
        current_close = self.data.Close[-1]
        
        # 🚀 LAUNCH CHECK: No active positions
        if not self.position:
            # 🌙 MOON DEV ENTRY CRITERIA 🌙
            if (abs(self.correlation[-1]) <= 0.05 and 
                self.data.High[-1] >= self.daily_high[-1]):
                
                stop_loss = self.next_day_high[-1]
                take_profit = self.next_day_low[-1]
                
                # 🛡️ Risk forcefield
                risk_amount = self.equity * self.risk_per_trade
                risk_per_share = stop_loss - current_close
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    if position_size > 0:
                        self.sell(size=position_size,
                                sl=stop_loss,
                                tp=take_profit,
                                tag="Moon Beam Short")
                        print(f"🚀 MOON DEATH STAR DEPLOYED! Short {position_size} units")
                        print(f"   Entry: {current_close:.2f} ✨ SL: {stop_loss:.2f} 🌙 TP: {take_profit:.2f}")
        else:
            # 🌘 GRAVITY WELL EXIT CHECK
            if self.position.pl <= -self.position.size * (self.position.sl - self.position.entry_price):
                print(f"🌑 BLACK HOLE ACTIVATED! Emergency exit at {current_close:.2f}")

# 🌟 STARLIGHT BACKTEST LAUNCH SEQUENCE 🌟
bt = Backtest(data, 
             CorrelationReversal, 
             cash=1_000_000, 
             commission=.002, 
             margin=1.0)

stats = bt.run()
print("🌕🌖🌗🌘🌑🌒🌓🌔🌕 MOON DEV BACKTEST RESULTS 🌕🌖🌗🌘🌑🌒🌓🌔🌕")
print(stats)
print(stats._strategy)