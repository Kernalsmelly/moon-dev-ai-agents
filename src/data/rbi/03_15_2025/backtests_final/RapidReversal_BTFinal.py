from backtesting import Backtest, Strategy
import pandas as pd
import talib
import numpy as np

class RapidReversal(Strategy):
    def init(self):
        # Calculate indicators using TA-Lib with self.I()
        self.rsi = self.I(talib.RSI, self.data.Close, 14, name='RSI')
        
        # MACD components
        macd, macd_signal, macd_hist = talib.MACD(self.data.Close, 12, 26, 9)
        self.macd_hist = self.I(lambda: macd_hist, name='MACD Hist')
        
        # ATR for risk management
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14, name='ATR')
        
        # Trade tracking
        self.trades_today = 0
        self.current_date = None

    def next(self):
        # Moon Dev Time Management 🌙⏳
        current_date = self.data.index[-1].date()
        if current_date != self.current_date:
            self.current_date = current_date
            self.trades_today = 0
            print(f"\n🌕 New Moon Day: {current_date.strftime('%Y-%m-%d')} | Trades reset to zero 🗓️")

        # Max 3 trades/day rule
        if self.trades_today >= 3:
            return

        # Entry Logic 🌟
        if not self.position:
            # RSI Oversold Check
            if len(self.rsi) < 2 or self.rsi[-1] >= 20:
                return
            
            # MACD Histogram Momentum Check
            if len(self.macd_hist) < 5:
                return
            
            current_slope = self.macd_hist[-1] - self.macd_hist[-2]
            prev_slopes = [self.macd_hist[-i-1] - self.macd_hist[-i-2] for i in range(2,4)]
            avg_slope = np.mean(prev_slopes) if prev_slopes else 0
            
            if current_slope > avg_slope and current_slope > 0:
                # Moon Dev Risk Calculator 🌙🧮
                atr_value = self.atr[-1]
                if atr_value <= 0:
                    return
                
                entry_price = self.data.Close[-1]
                stop_loss = entry_price - 0.75 * atr_value
                take_profit = entry_price + 1.5 * atr_value
                
                # Position Sizing Magic 🌙✨
                risk_amount = 0.01 * self.equity
                position_size = risk_amount / (0.75 * atr_value)
                position_size = int(round(position_size))
                
                if position_size > 0:
                    self.buy(size=position_size, sl=stop_loss, tp=take_profit)
                    self.trades_today += 1
                    print(f"🚀 MOON SHOT! Entry: {entry_price:.2f} | Size: {position_size} | SL: {stop_loss:.2f} | TP: {take_profit:.2f} 🌌")

        # Exit Logic 🌗
        else:
            # RSI Exit Condition
            if len(self.rsi) >= 2 and self.rsi[-2] <= 50 and self.rsi[-1] > 50:
                self.position.close()
                print(f"🌑 DARK MOON EXIT! RSI Crossed 50 | Price: {self.data.Close[-1]:.2f} | P/L: {self.position.pl:.2f} 💸")

# Data Preparation Ritual 🌙🔮
data = pd.read_csv('your_data.csv', parse_dates=['Date'])
data.set_index('Date', inplace=True)

# Backtest Configuration 🌙⚙️
bt = Backtest(data, RapidReversal, cash=10000, commission=.002)

# Launch Backtest 🚀🌙
stats = bt.run()
print("\n🌙✨ MOON DEV BACKTEST COMPLETE! ✨🌙")
print(stats)
bt.plot()