from backtesting import Backtest, Strategy
import pandas as pd
import talib

class LiquidationBreakout(Strategy):
    def init(self):
        # 🌙 Moon Dev Indicators Setup
        self.liquidation_high = self.I(talib.MAX, self.data.High, timeperiod=20, name='🔥 Liquidation High')
        self.liquidation_low = self.I(talib.MIN, self.data.Low, timeperiod=20, name='❄️ Liquidation Low')
        self.std_dev = self.I(talib.STDDEV, self.data.Close, timeperiod=4, nbdev=1, name='📊 1H Volatility')
        self.volatility_sma = self.I(talib.SMA, self.std_dev, timeperiod=20, name='📈 Volatility SMA')
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, timeperiod=14, name='🎯 ATR(14)')

    def next(self):
        current_close = self.data.Close[-1]
        moon_debug = f"🌙 Close: {current_close:.2f} | LH: {self.liquidation_high[-1]:.2f} | LL: {self.liquidation_low[-1]:.2f}"
        print(moon_debug)

        if not self.position:
            # 🚀 Long Entry Logic
            if (current_close > self.liquidation_high[-1] 
                and self.std_dev[-1] < self.volatility_sma[-1]):
                
                risk_amount = self.equity * 0.01  # 1% risk
                atr_value = self.atr[-1]
                
                if atr_value > 0:
                    position_size = int(round(risk_amount / atr_value))
                    if position_size > 0:
                        entry_price = current_close
                        sl_price = entry_price - atr_value
                        tp_price = entry_price + 2*atr_value
                        self.buy(size=position_size, sl=sl_price, tp=tp_price)
                        print(f"🚀🌕 LONG! Size: {position_size} | Entry: {entry_price:.2f}")
                        print(f"   TP: {tp_price:.2f} | SL: {sl_price:.2f}")

            # 📉 Short Entry Logic
            elif (current_close < self.liquidation_low[-1] 
                  and self.std_dev[-1] < self.volatility_sma[-1]):
                  
                risk_amount = self.equity * 0.01  # 1% risk
                atr_value = self.atr[-1]
                
                if atr_value > 0:
                    position_size = int(round(risk_amount / atr_value))
                    if position_size > 0:
                        entry_price = current_close
                        sl_price = entry_price + atr_value
                        tp_price = entry_price - 2*atr_value
                        self.sell(size=position_size, sl=sl_price, tp=tp_price)
                        print(f"📉🌑 SHORT! Size: {position_size} | Entry: {entry_price:.2f}")
                        print(f"   TP: {tp_price:.2f} | SL: {sl_price:.2f}")
        else:
            # 🌪️ Volatility Expansion Exit
            if self.std_dev[-1] > self.volatility_sma[-1]:
                print(f"🌪️✨ Volatility Spike! Closing at {current_close:.2f}")
                self.position.close()

# 🌙 Data Preparation Ritual
data = pd.read_csv(
    '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv',
    parse_dates=['datetime'],
    index_col='datetime'
)

# 🧹 Data Cleansing
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col])

# 🔄 Column Alchemy
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

# 🚀 Launch Backtest
bt = Backtest(data, LiquidationBreakout, cash=1_000_000, commission=.002)
stats = bt.run()

# 🌕 Moon Dev Performance Report
print("\n" + "="*50)
print("🌙✨ Moon Dev Strategy Report")
print("="*50)
print(stats)
print(stats._strategy)