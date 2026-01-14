#!/usr/bin/env python3
# 🌙 AI8 - MoonDev Supreme Strategy - Beat Buy & Hold with 5+ Trades
# Moon Dev Trading Command Center - FINAL BOSS STRATEGY

import pandas as pd
from backtesting import Backtest, Strategy
import talib

# Load and preprocess data with lunar precision 🌕
data = pd.read_csv('/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv')

# Clean data columns with cosmic care ✨
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])

# Rename columns to match backtesting requirements
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# Convert datetime and set index with lunar alignment 🌑
data['datetime'] = pd.to_datetime(data['datetime'])
data.set_index('datetime', inplace=True)

print(f"🌙 Data loaded: {len(data)} rows from {data.index[0]} to {data.index[-1]}")

class MoonDevSupreme(Strategy):
    def init(self):
        print("🌙 Initializing MoonDev SUPREME strategy...")
        
        # Supreme trend detection system 🚀
        self.ema12 = self.I(talib.EMA, self.data.Close, 12)
        self.ema26 = self.I(talib.EMA, self.data.Close, 26)
        self.ema50 = self.I(talib.EMA, self.data.Close, 50)
        self.rsi = self.I(talib.RSI, self.data.Close, 14)
        self.macd, self.macd_signal, _ = self.I(talib.MACD, self.data.Close, 12, 26, 9)
        
        # Volume and momentum
        self.volume_sma = self.I(talib.SMA, self.data.Volume, 20)
        self.atr = self.I(talib.ATR, self.data.High, self.data.Low, self.data.Close, 14)
        
        # Bollinger Bands for volatility
        self.bb_upper, self.bb_middle, self.bb_lower = self.I(talib.BBANDS, self.data.Close, 20, 2, 2)
        
        self.trade_count = 0
        self.last_trade_date = None
        
        print("✨ Supreme indicators initialized!")

    def next(self):
        if len(self.data) < 100:
            return
            
        current_date = self.data.index[-1].date()
        current_price = self.data.Close[-1]
        
        # Supreme entry logic - Multiple opportunities 🎯
        if not self.position and self.trade_count < 8:  # Allow 8 strategic trades
            
            # Time filter - at least 30 days between trades for quality
            time_ok = True
            if self.last_trade_date:
                days_diff = (current_date - self.last_trade_date).days
                time_ok = days_diff >= 30
            
            if not time_ok:
                return
            
            # Multi-factor entry conditions
            ema_bullish = self.ema12[-1] > self.ema26[-1] > self.ema50[-1]
            macd_positive = self.macd[-1] > 0 and self.macd[-1] > self.macd_signal[-1]
            rsi_good = 30 < self.rsi[-1] < 70  # Healthy range
            volume_good = self.data.Volume[-1] > self.volume_sma[-1]
            
            # Price action setups
            above_bb_middle = current_price > self.bb_middle[-1]
            pullback_to_ema = (current_price > self.ema12[-1] * 0.995 and 
                              self.data.Close[-2] < self.ema12[-2])
            
            # Breakout setup
            recent_high = max(self.data.High[-10:])
            breakout = current_price > recent_high * 1.001
            
            # Strong momentum setup
            price_momentum = current_price > self.data.Close[-5] * 1.02  # 2% move in 5 bars
            
            # Entry conditions (any of these setups)
            setup1 = ema_bullish and macd_positive and rsi_good and pullback_to_ema
            setup2 = ema_bullish and breakout and volume_good and rsi_good
            setup3 = price_momentum and macd_positive and above_bb_middle and volume_good
            
            if setup1 or setup2 or setup3:
                # Strategic position sizing for maximum gains 💪
                risk_pct = 0.30 + (0.05 * min(self.trade_count, 3))  # Increase risk with experience
                risk_amount = self.equity * risk_pct
                
                # ATR-based position sizing
                atr_value = self.atr[-1] if len(self.atr) > 0 else current_price * 0.025
                stop_distance = atr_value * 2.5  # Reasonable stop
                
                position_size = int(risk_amount / stop_distance)
                
                # Cap position for safety
                max_position_value = self.equity * 0.85
                max_size = int(max_position_value / current_price)
                position_size = min(position_size, max_size)
                
                if position_size > 0:
                    stop_loss = current_price - stop_distance
                    
                    self.buy(size=position_size, sl=stop_loss)
                    self.trade_count += 1
                    self.last_trade_date = current_date
                    
                    setup_name = "PULLBACK" if setup1 else ("BREAKOUT" if setup2 else "MOMENTUM")
                    print(f"🚀👑 SUPREME ENTRY #{self.trade_count}! {setup_name}")
                    print(f"   💎 Size: {position_size} @ {current_price:.2f} (Risk: {risk_pct*100:.0f}%)")
                    print(f"   📊 RSI: {self.rsi[-1]:.1f}, MACD: {self.macd[-1]:.3f}")
                    print(f"   🛡️ Stop: {stop_loss:.2f}")

        # Supreme exit logic - Maximize gains 🌊
        else:
            if self.position:
                entry_price = self.position.entry_price if hasattr(self.position, 'entry_price') else current_price
                profit_pct = (current_price - entry_price) / entry_price
                
                # Trend reversal signals
                ema_bearish = self.ema12[-1] < self.ema26[-1]
                macd_bearish = self.macd[-1] < self.macd_signal[-1] and self.macd[-1] < 0
                rsi_extreme = self.rsi[-1] > 80 or self.rsi[-1] < 20
                
                # Dynamic profit targets based on trade count
                profit_target_base = 0.20  # 20% base target
                profit_target = profit_target_base + (0.05 * self.trade_count)  # Increase with experience
                
                # Exit conditions
                big_profit = profit_pct > profit_target
                trend_reversal = ema_bearish and macd_bearish
                take_profit_on_weakness = profit_pct > 0.10 and (ema_bearish or macd_bearish)
                stop_big_loss = profit_pct < -0.08  # 8% max loss
                
                if big_profit or trend_reversal or take_profit_on_weakness or stop_big_loss:
                    self.position.close()
                    exit_reason = ("BIG PROFIT" if big_profit else 
                                 ("TREND REVERSAL" if trend_reversal else
                                  ("PROFIT ON WEAKNESS" if take_profit_on_weakness else "STOP LOSS")))
                    print(f"💰 SUPREME EXIT! {exit_reason}")
                    print(f"   🎯 P&L: {profit_pct*100:.1f}% @ {current_price:.2f}")

if __name__ == "__main__":
    print("🌙🚀 Starting MoonDev SUPREME Backtest...")
    
    # Run backtest with lunar power 🌕
    bt = Backtest(data, MoonDevSupreme, cash=1_000_000, commission=.002)
    stats = bt.run()
    
    # Print full statistics with cosmic flair ✨
    print("\n🌕🌖🌗🌘🌑🌒🌓🌔 SUPREME MOONDEV STATS 🌕🌖🌗🌘🌑🌒🌓🌔")
    print(stats)
    print(f"\n🎯 Target: Beat Buy & Hold {stats['Buy & Hold Return [%]']:.1f}%")
    print(f"🚀 Strategy Return: {stats['Return [%]']:.1f}%")
    print(f"📊 Number of Trades: {stats['# Trades']}")
    print(f"🏆 Win Rate: {stats['Win Rate [%]']:.1f}%")
    print(f"📈 Sharpe Ratio: {stats['Sharpe Ratio']:.2f}")
    print(f"📉 Max Drawdown: {stats['Max. Drawdown [%]']:.1f}%")
    
    if stats['Return [%]'] > stats['Buy & Hold Return [%]'] and stats['# Trades'] >= 5:
        print("\n🏆🌙 VICTORY! SUPREME STRATEGY BEATS BUY & HOLD WITH 5+ TRADES! 🚀✨")
        print("🎉 MISSION ACCOMPLISHED! 🎉")
    else:
        print("\n🔄 Continuing optimization...")
    
    print("\n🌙 MoonDev SUPREME Backtest Complete! 🚀")