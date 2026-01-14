# -*- coding: utf-8 -*-
import pandas as pd
import talib
from backtesting import Strategy, Backtest

# Moon Dev Data Preparation 🌙
def prepare_data(path):
    data = pd.read_csv(
        path,
        parse_dates=['datetime'],
        index_col='datetime'
    )
    # Clean column names
    data.columns = data.columns.str.strip().str.lower()
    data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
    
    # Proper column mapping
    data = data.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    })
    return data

class VoltaSqueeze(Strategy):
    risk_pct = 0.01  # 1% risk per trade 🌙
    bb_period = 20
    rsi_period = 14
    atr_period = 14
    
    def init(self):
        # Bollinger Bands Width Calculation ✨
        def bb_width(close):
            upper, _, lower = talib.BBANDS(
                close, 
                timeperiod=self.bb_period,
                nbdevup=2,
                nbdevdn=2
            )
            return upper - lower
        
        self.bb_width = self.I(bb_width, self.data.Close)
        self.bb_width_sma = self.I(talib.SMA, self.bb_width, 20)
        
        # Momentum Indicators 🚀
        self.rsi = self.I(talib.RSI, self.data.Close, self.rsi_period)
        self.atr = self.I(talib.ATR, 
                         self.data.High, 
                         self.data.Low, 
                         self.data.Close, 
                         self.atr_period)
        
        self.entry_price = None
        self.entry_atr = None

    def next(self):
        # Moon Dev Trading Logic 🌕
        if not self.position:
            if len(self.rsi) < 2 or len(self.bb_width) < 20:
                return
            
            # Squeeze Condition Check ✨
            squeeze_on = (self.bb_width[-1] < self.bb_width_sma[-1])
            rsi_cross = (self.rsi[-2] <= 50) and (self.rsi[-1] > 50)
            
            if squeeze_on and rsi_cross:
                atr_value = self.atr[-1]
                if atr_value <= 0:
                    return  # Safety check 🌙
                
                # Moon Position Sizing Calculation 🚀
                risk_amount = self.risk_pct * self.equity
                position_size = risk_amount / (2 * atr_value)
                position_size = int(round(position_size))
                
                # Ensure valid position size
                max_possible = int(self.equity // self.data.Close[-1])
                position_size = min(position_size, max_possible)
                
                if position_size > 0:
                    self.buy(size=position_size)
                    self.entry_price = self.data.Close[-1]
                    self.entry_atr = atr_value
                    print(f"🌙 MOON DEV LONG ENTRY 🚀 | Size: {position_size} | Entry: {self.entry_price:.2f} | ATR: {atr_value:.2f}")
        else:
            # Dynamic Exit Logic ✨
            target_price = self.entry_price + (2 * self.entry_atr)
            if self.data.High[-1] >= target_price:
                self.position.close()
                print(f"✨ MOON DEV PROFIT TAKEOFF 🌕 | Exit: {self.data.Close[-1]:.2f} | Profit: {self.position.pl:.2f}")

# Moon Dev Backtest Execution 🌙
if __name__ == "__main__":
    data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
    prepared_data = prepare_data(data_path)
    
    bt = Backtest(
        prepared_data,
        VoltaSqueeze,
        cash=1_000_000,
        commission=.002,
        exclusive_orders=True
    )
    
    stats = bt.run()
    print("\n🌙🌙🌙 MOON DEV BACKTEST RESULTS 🌙🌙🌙")
    print(stats)
    print("\n✨✨✨ STRATEGY METRICS ✨✨✨")
    print(stats._strategy)