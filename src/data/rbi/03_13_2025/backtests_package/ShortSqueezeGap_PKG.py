# 🌙 Moon Dev's Short Squeeze Gap Backtest 🌙
from backtesting import Strategy
import talib
import pandas as pd
import numpy as np

# 🚀 DATA PREPARATION 🚀
data = pd.read_csv(
    '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
    parse_dates=['datetime']
)

# 🌙 Clean and format columns
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.set_index('datetime', inplace=True)
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class ShortSqueezeGap(Strategy):
    # 🌕 STRATEGY PARAMETERS 🌕
    gap_threshold = 0.01  # 1% gap down required
    volume_multiplier = 1.5  # 150% of average volume
    rsi_period = 14
    rsi_oversold = 30
    risk_pct = 0.02  # 2% risk per trade
    reward_ratio = 2  # 2:1 risk:reward
    max_bars_held = 20  # 5 hours (15m intervals)
    
    def init(self):
        # 🌌 INDICATOR CALCULATIONS 🌌
        # Previous close shifted by 1 period
        self.prev_close = self.I(
            lambda x: np.concatenate([[np.nan], x[:-1]]),
            self.data.Close,
            name='Prev Close'
        )
        
        # Volume confirmation indicators
        self.vol_sma = self.I(talib.SMA, self.data.Volume, timeperiod=20, name='Vol SMA 20')
        self.rsi = self.I(talib.RSI, self.data.Close, timeperiod=self.rsi_period, name='RSI 14')
        
        # ✨ MOON DEV DEBUG TRACKERS ✨
        self.trade_count = 0
        self.entry_prices = []
        self.exit_prices = []

    def next(self):
        current_bar = len(self.data) - 1
        
        # 🌑 MOON PHASE: NO ACTIVE POSITIONS 🌑
        if not self.position:
            # Early exit for insufficient data
            if current_bar < 20 or np.isnan(self.prev_close[-1]):
                return
                
            # 🌟 ENTRY CONDITIONS 🌟
            gap_down = self.data.Open[-1] < self.prev_close[-1] * (1 - self.gap_threshold)
            volume_spike = self.data.Volume[-1] > self.vol_sma[-1] * self.volume_multiplier
            rsi_condition = self.rsi[-1] < self.rsi_oversold
            
            if gap_down and volume_spike and rsi_condition:
                # 🌙 RISK MANAGEMENT CALCULATIONS 🌙
                equity = self.equity
                entry_price = self.data.Close[-1]
                stop_loss = entry_price * (1 - (self.risk_pct/self.reward_ratio))
                take_profit = entry_price * (1 + self.risk_pct)
                
                # 🚀 POSITION SIZING CALCULATION 🚀
                risk_amount = equity * self.risk_pct
                risk_per_share = entry_price - stop_loss
                position_size = risk_amount / risk_per_share
                position_size = int(round(position_size))
                
                if position_size > 0:
                    # 🌕 MOON ENTRY SIGNAL 🌕
                    self.buy(
                        size=position_size,
                        sl=stop_loss,
                        tp=take_profit,
                        tag=f"MOON ROCKET #{self.trade_count+1}"
                    )
                    self.trade_count += 1
                    self.entry_prices.append(entry_price)
                    print(f"🌕 MOON ENTRY #{self.trade_count} 🌕")
                    print(f"📈 Price: {entry_price:.2f} | 📦 Size: {position_size}")
                    print(f"🛑 SL: {stop_loss:.2f} | 🎯 TP: {take_profit:.2f}")
                    print(f"💵 Equity: {equity:.2f}")