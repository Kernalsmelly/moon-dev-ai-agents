# 🌙 Moon Dev's Volatility Contraction Backtest 🌙
from backtesting import Strategy, Backtest
import pandas as pd
import talib

# 🚀 Data Preparation
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"
data = pd.read_csv(data_path)

# Clean and prepare data according to Moon Dev specs
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)
data['DateTime'] = pd.to_datetime(data['datetime'])
data.set_index('DateTime', inplace=True)

class VolatilityContraction(Strategy):
    def init(self):
        # 🌗 Moon Dev Indicators
        self.middle_band = self.I(talib.SMA, self.data.Close, 20, name='SMA20')
        self.std_dev = self.I(talib.STDDEV, self.data.Close, 20, name='STD20')
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14, name='ATR14')
        self.hist_high_atr = self.I(talib.MAX, self.atr, 50, name='ATR_HIGH_50')
        self.recent_high = self.I(talib.MAX, self.data.High, 20, name='HIGH_20')
        
        # Track peak volatility after entry
        self.peak_atr_since_entry = 0

    def next(self):
        # 🌑 Skip if not enough data
        if len(self.data) < 50:
            return

        # 🌓 Calculate current indicators
        upper_band = self.middle_band[-1] + 2*self.std_dev[-1]
        lower_band = self.middle_band[-1] - 2*self.std_dev[-1]
        bandwidth = ((upper_band - lower_band)/self.middle_band[-1])*100
        atr_ratio = self.atr[-1]/self.hist_high_atr[-1] if self.hist_high_atr[-1] != 0 else 0

        # 🌗 Moon Dev Entry Logic
        if not self.position:
            # Volatility contraction conditions
            if (atr_ratio >= 0.95) and (bandwidth < 2):
                # 🌕 Risk Management Calculations
                risk_pct = 0.01  # 1% equity risk
                stop_loss = self.recent_high[-1]
                risk_amount = self.equity * risk_pct
                risk_per_share = stop_loss - self.data.Close[-1]
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    if position_size > 0:
                        self.sell(size=position_size, sl=stop_loss, 
                                 tag="🌙 MOON DEV SHORT ENTRY")
                        self.peak_atr_since_entry = self.atr[-1]
                        print(f"🌙✨ MOON DEV ALERT: Short initiated {position_size} units "
                              f"at {self.data.Close[-1]:.2f} with SL {stop_loss:.2f} 🚀")

        # 🌓 Exit Logic
        else:
            # Update volatility peak
            self.peak_atr_since_entry = max(self.peak_atr_since_entry, self.atr[-1])
            
            # Price breakout exit
            current_high = self.data.High[-1]
            current_low = self.data.Low[-1]
            if current_high >= upper_band or current_low <= lower_band:
                self.position.close()
                print(f"✨🌑 MOON DEV EXIT: Price breakout at {self.data.Close[-1]:.2f}")

            # Volatility collapse exit
            if self.atr[-1] < 0.8 * self.peak_atr_since_entry:
                self.position.close()
                print(f"🌗🚀 MOON DEV EXIT: Volatility collapse at {self.data.Close[-1]:.2f}")

# 🌕 Launch Moon Dev Backtest
bt = Backtest(data, VolatilityContraction, cash=1_000_000, commission=.002)
stats = bt.run()
print(stats)
print(stats._strategy)