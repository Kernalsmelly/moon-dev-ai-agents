from backtesting import Backtest, Strategy
import pandas as pd
import talib

# Load and preprocess data
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean column names and handle data
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

class ATRRatioReversal(Strategy):
    risk_pct = 0.01  # 1% risk per trade 🌙
    atr_period = 14
    tp_multiplier = 2
    sl_multiplier = 1

    def init(self):
        # Moon Dev Indicator Calculation ✨
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 
                         timeperiod=self.atr_period, name='ATR_14')
        self.ratio = self.I(lambda c, a: c/a, self.data.Close, self.atr, name='Close/ATR')
        print("🌙✨ Lunar indicators initialized! Ready for launch!")

    def next(self):
        price = self.data.Close[-1]
        atr_value = self.atr[-1]
        
        # Moon Dev Entry Logic 🚀
        if not self.position and self.ratio[-1] < 0:
            # Risk Management Calculations 🌙
            sl_price = price - atr_value * self.sl_multiplier
            tp_price = price + atr_value * self.tp_multiplier
            risk_amount = self.risk_pct * self.equity
            risk_per_share = price - sl_price
            
            if risk_per_share > 0:
                position_size = int(round(risk_amount / risk_per_share))
                
                if position_size > 0:
                    self.buy(size=position_size, sl=sl_price, tp=tp_price)
                    print(f"🌙🚀 LAUNCHING LONG! Size: {position_size} | "
                          f"Entry: {price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}")

        # Moon Dev Exit Check 🌕
        if self.position and self.position.pl_pct >= 0.5:
            self.position.close()
            print(f"🌕💰 Cosmic profit taken! PL: {self.position.pl:.2f}")

# Execute Moon Dev Backtest 🌙✨
bt = Backtest(data, ATRRatioReversal, cash=1_000_000, margin=1.0, trade_on_close=True)
stats = bt.run()

# Print Moon Dev Analytics ✨
print("\n🌙===== MOON DEV FINAL STATS =====🌙")
print(stats)
print("\n🌙===== STRATEGY DETAILS =====🌙")
print(stats._strategy)