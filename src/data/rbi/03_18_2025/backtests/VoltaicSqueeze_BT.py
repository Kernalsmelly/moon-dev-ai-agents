# 🌙 MOON DEV BACKTESTING SCRIPT FOR VOLTAIC SQUEEZE STRATEGY 🚀

import pandas as pd
from backtesting import Backtest, Strategy
import talib as ta

class VoltaicSqueeze(Strategy):
    def init(self):
        # 🌟 INDICATOR CALCULATION PHASE ✨
        # Bollinger Bands (20,2)
        self.I(lambda c: ta.BBANDS(c, 20, 2, 2)[0], self.data.Close, name='BB_UPPER')
        self.I(lambda c: ta.BBANDS(c, 20, 2, 2)[2], self.data.Close, name='BB_LOWER')
        
        # BB Width Calculation
        self.I(lambda u, l: u - l, self.data.BB_UPPER, self.data.BB_LOWER, name='BB_WIDTH')
        
        # 30-period BB Width Minimum 🌙
        self.I(ta.MIN, self.data.BB_WIDTH, 30, name='MIN_BB_WIDTH_30')
        
        # RSI (14) and ATR (14)
        self.I(ta.RSI, self.data.Close, 14, name='RSI_14')
        self.I(ta.ATR, self.data.High, self.data.Low, self.data.Close, 14, name='ATR_14')

    def next(self):
        # 🌌 STRATEGY LOGIC CORE 🌠
        if len(self.data) < 30:  # Warm-up period
            return

        # 🔍 SIGNAL DETECTION
        bb_contraction = (self.data.BB_WIDTH[-1] == self.data.MIN_BB_WIDTH_30[-1])
        rsi_cross = (self.data.RSI_14[-2] <= 50) and (self.data.RSI_14[-1] > 50)
        
        # 🚀 ENTRY LOGIC
        if not self.position and bb_contraction and rsi_cross:
            atr = self.data.ATR_14[-1]
            if atr == 0:  # Safety check
                return
                
            # 🌕 POSITION SIZING CALCULATION
            risk_amount = self.equity * 0.01  # 1% risk
            position_size = int(round(risk_amount / atr))
            
            if position_size > 0:
                entry_price = self.data.Close[-1]
                stop_loss = entry_price - atr
                take_profit = entry_price + 2*atr
                
                self.buy(
                    size=position_size,
                    sl=stop_loss,
                    tp=take_profit,
                    tag="Voltaic Squeeze Entry 🌙"
                )
                print("🌕 MOON DEV ALERT: Cosmic Alignment Detected! 🚀")
                print(f"   ENTRY: {entry_price:.2f} | SIZE: {position_size}")
                print(f"   STAR MAP: SL {stop_loss:.2f} | TP {take_profit:.2f} 🌌")

        # 🛑 EXIT LOGIC
        elif self.position:
            if self.data.RSI_14[-1] < 50:
                self.position.close()
                print(f"🌑 MOON RETREAT: RSI Descending - Closing Position at {self.data.Close[-1]:.2f} 🌘")

# 🌍 DATA PREPARATION 
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path)

# 🧹 DATA CLEANSING RITUAL
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})
data['datetime'] = pd.to_datetime(data['datetime'])
data = data.set_index('datetime')

# 🌙 BACKTEST LAUNCH SEQUENCE
bt = Backtest(data, VoltaicSqueeze, cash=1_000_000, commission=.002)
stats = bt.run()

# 🪐 RESULTS TRANSMISSION
print("\n🌠🌠🌠 MOON DEV STRATEGY TELEMETRY 🌠🌠🌠")
print(stats)
print(stats._strategy)
print("✨ Strategy Execution Complete - May the Cosmic Trends Be With You! 🌙")