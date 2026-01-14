import pandas as pd
import talib
import pandas_ta as ta
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

# Load and preprocess data
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')

# Clean column names and prepare data
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data = data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
})

class VortexCompression(Strategy):
    risk_per_trade = 0.01  # 1% risk per trade
    
    def init(self):
        # Calculate Vortex Indicator 🌪️
        vi = ta.vortex(high=self.data.High, low=self.data.Low, close=self.data.Close, length=14)
        self.vi_plus = self.I(lambda: vi['VORTIC_14'], name='VI+')
        self.vi_minus = self.I(lambda: vi['VORTID_14'], name='VI-')
        
        # Bollinger Bands 📉
        bb_upper, bb_middle, bb_lower = talib.BBANDS(self.data.Close, timeperiod=20, nbdevup=2, nbdevdn=2)
        self.bb_upper = self.I(lambda: bb_upper, name='BB_Upper')
        self.bb_lower = self.I(lambda: bb_lower, name='BB_Lower')
        
        # Bollinger Band Width 📏
        bbw = (bb_upper - bb_lower) / bb_middle
        self.bbw = self.I(lambda: bbw, name='BBW')
        self.bbw_ma = self.I(talib.SMA, bbw, timeperiod=10, name='BBW_MA')
        
        # Volume Surge 🌊
        self.volume_ma = self.I(talib.SMA, self.data.Volume, 20, name='Volume_MA')
        
        # Swing Low Detection 🔍
        self.swing_low = self.I(talib.MIN, self.data.Low, 20, name='Swing_Low')
        
        print("🌙✨ Moon Dev Indicators Activated! Vortex spirals engaged! 🚀")

    def next(self):
        if not self.position:
            # Entry Conditions Check 🔎
            vi_crossover = crossover(self.vi_plus, self.vi_minus)
            volume_surge = self.data.Volume[-1] > self.volume_ma[-1]
            volatility_contraction = self.bbw[-1] < self.bbw_ma[-1]
            
            if vi_crossover and volume_surge and volatility_contraction:
                # Calculate Risk Parameters ⚖️
                sl_price = max(self.bb_lower[-1], self.swing_low[-1])
                entry_price = self.data.Close[-1]  # Conservative estimate
                risk_per_share = entry_price - sl_price
                
                if risk_per_share <= 0:
                    print("🌑 Moon Dev Alert: Negative risk detected! Aborting launch!")
                    return
                
                risk_amount = self.equity * self.risk_per_trade
                position_size = int(round(risk_amount / risk_per_share))
                
                if position_size > 0:
                    self.buy(size=position_size, 
                            sl=sl_price,
                            tp=self.bb_upper[-1])
                    print(f"🚀🌙 MOON DEV LAUNCH! Long {position_size} units | Entry: {entry_price:.2f} | SL: {sl_price:.2f} | TP: {self.bb_upper[-1]:.2f}")
        else:
            # Automatic exit via SL/TP 🎯
            pass

# Execute backtest 🌕
bt = Backtest(data, VortexCompression, cash=1_000_000, trade_on_close=False)
stats = bt.run()
print("\n" + "="*55 + "\n🌕🌖🌗🌘🌑 MOON DEV BACKTEST RESULTS 🌑🌘🌗🌖🌕")
print(stats)
print(stats._strategy)