# 🌙 MOON DEV BACKTESTING IMPLEMENTATION 🌙
import pandas as pd
import talib
from backtesting import Strategy

# 🚀 DATA PREPARATION 🚀
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'
data = pd.read_csv(data_path)

# 🌙 Clean and prepare columns as per Moon Dev standards
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

class BandedMeanTarget(Strategy):
    risk_per_trade = 0.02  # 🌕 2% risk per trade
    
    def init(self):
        # 🌗 INDICATOR CALCULATION WITH MOON DEV MAGIC 🌗
        self.hist_high = self.I(lambda: self.data.High.expanding().max(), name='Historical High')
        
        # 🌈 BOLLINGER BANDS CALCULATION
        bb_upper, _, bb_lower = talib.BBANDS(self.data.Close, 
                                            timeperiod=20, 
                                            nbdevup=2, 
                                            nbdevdn=2)
        self.bb_upper = self.I(lambda: bb_upper, name='BB Upper')
        self.bb_lower = self.I(lambda: bb_lower, name='BB Lower')
        
        # 🌊 BAND WIDTH ANALYSIS
        band_width = bb_upper - bb_lower
        sma_width = talib.SMA(band_width, timeperiod=20)
        self.band_width = self.I(lambda: band_width, name='Band Width')
        self.sma_width = self.I(lambda: sma_width, name='SMA Width')

    def next(self):
        current_close = self.data.Close[-1]
        hist_high = self.hist_high[-1]
        bb_lower = self.bb_lower[-1]
        
        # 🌙 ENTRY STRATEGY: MEAN REVERSION WITH BOLLINGER CONFIRMATION
        if not self.position:
            entry_condition = (
                current_close <= 0.75 * hist_high and
                current_close > bb_lower
            )
            
            if entry_condition:
                # 🚀 RISK MANAGEMENT CALCULATION
                entry_price = current_close
                initial_stop = entry_price * 0.5  # 🌑 50% initial stop
                risk_amount = self.equity * self.risk_per_trade
                risk_per_share = entry_price - initial_stop
                
                if risk_per_share > 0:
                    position_size = int(round(risk_amount / risk_per_share))
                    if position_size > 0:
                        self.buy(size=position_size)
                        print(f"🌙✨ MOON DEV ENTRY! Buying {position_size} @ {entry_price:.2f} 🚀")

        # 🌕 EXIT STRATEGY: DYNAMIC TARGETS & STOP MANAGEMENT
        else:
            # 💰 TAKE PROFIT LOGIC
            tp_price = 1.25 * self.hist_high[-1]
            if self.data.High[-1] >= tp_price:
                self.position.close()
                print(f"🌕💎 PROFIT TAKEN @ {tp_price:.2f}! MOON MISSION SUCCESS! 💰")

            # 🛡️ DYNAMIC STOP UPDATE
            current_width = self.band_width[-1]
            sma_width = self.sma_width[-1]
            
            if current_width > sma_width:
                stop_pct = 0.4  # 🌪️ Volatile market
            elif current_width < sma_width:
                stop_pct = 0.6  # 🧊 Stable market
            else:
                stop_pct = 0.5  # 🌗 Normal conditions
                
            new_stop = self.position.entry_price * (1 - stop_pct)
            self.position.sl = new_stop
            
            # 🔄 STOP LOSS CHECK
            if self.data.Low[-1] <= new_stop:
                self.position.close()
                print(f"🌑🛡️ STOP LOSS HIT @ {new_stop:.2f}! PROTECTING MOON BASE! 🛡️")