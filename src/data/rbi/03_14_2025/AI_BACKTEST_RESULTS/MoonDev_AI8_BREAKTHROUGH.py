#!/usr/bin/env python3
# 🌙 AI8 - MoonDev BREAKTHROUGH Strategy - Simple Monthly Trend Following
# Moon Dev Trading Command Center - THE BREAKTHROUGH

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

class MoonDevBreakthrough(Strategy):
    def init(self):
        print("🌙 Initializing MoonDev BREAKTHROUGH strategy...")
        
        # Simple monthly trend system 🚀
        self.sma20 = self.I(talib.SMA, self.data.Close, 20)
        self.sma50 = self.I(talib.SMA, self.data.Close, 50)
        self.rsi = self.I(talib.RSI, self.data.Close, 14)
        
        self.trade_count = 0
        self.bars_since_last_trade = 0
        
        print("✨ Breakthrough indicators ready!")

    def next(self):
        if len(self.data) < 60:
            return
            
        current_price = self.data.Close[-1]
        self.bars_since_last_trade += 1
        
        # Monthly trend following - simple but effective 🎯
        if not self.position and self.trade_count < 10:
            
            # Trade every month (about 2880 bars = 30 days * 24 hours * 4 quarters)
            if self.bars_since_last_trade < 2880:
                return
            
            # Simple trend following conditions
            price_above_sma20 = current_price > self.sma20[-1]
            sma20_above_sma50 = self.sma20[-1] > self.sma50[-1]
            rsi_healthy = self.rsi[-1] > 45  # Not oversold
            
            # Enter if trend is up
            if price_above_sma20 and sma20_above_sma50 and rsi_healthy:
                # Use ALL available capital for maximum returns
                position_size = int(self.equity / current_price)
                
                if position_size > 0:
                    # Simple stop loss
                    stop_loss = self.sma50[-1] * 0.95  # 5% below SMA50
                    
                    self.buy(size=position_size, sl=stop_loss)
                    self.trade_count += 1
                    self.bars_since_last_trade = 0
                    
                    print(f"🚀🌟 BREAKTHROUGH ENTRY #{self.trade_count}!")
                    print(f"   💎 Size: {position_size} @ {current_price:.2f}")
                    print(f"   📊 SMA20: {self.sma20[-1]:.0f}, SMA50: {self.sma50[-1]:.0f}")
                    print(f"   📈 RSI: {self.rsi[-1]:.1f}")
                    print(f"   🛡️ Stop: {stop_loss:.2f}")

        # Hold for trend - simple exits 🌊
        else:
            if self.position:
                entry_price = self.position.entry_price if hasattr(self.position, 'entry_price') else current_price
                profit_pct = (current_price - entry_price) / entry_price
                
                # Exit conditions
                price_below_sma20 = current_price < self.sma20[-1]
                sma20_below_sma50 = self.sma20[-1] < self.sma50[-1]
                big_profit = profit_pct > 0.80  # 80% profit target
                
                if price_below_sma20 or sma20_below_sma50 or big_profit:
                    self.position.close()
                    exit_reason = ("PRICE BELOW SMA20" if price_below_sma20 else
                                 ("SMA20 BELOW SMA50" if sma20_below_sma50 else "BIG PROFIT"))
                    print(f"💰 BREAKTHROUGH EXIT! {exit_reason}")
                    print(f"   🎯 P&L: {profit_pct*100:.1f}% @ {current_price:.2f}")

if __name__ == "__main__":
    print("🌙🚀 Starting MoonDev BREAKTHROUGH Backtest...")
    
    # Run backtest with lunar power 🌕
    bt = Backtest(data, MoonDevBreakthrough, cash=1_000_000, commission=.002)
    stats = bt.run()
    
    # Print full statistics with cosmic flair ✨
    print("\n🌕🌖🌗🌘🌑🌒🌓🌔 BREAKTHROUGH STATS 🌕🌖🌗🌘🌑🌒🌓🌔")
    print(stats)
    print(f"\n🎯 Target: Beat Buy & Hold {stats['Buy & Hold Return [%]']:.1f}%")
    print(f"🚀 Strategy Return: {stats['Return [%]']:.1f}%")
    print(f"📊 Number of Trades: {stats['# Trades']}")
    
    if stats['# Trades'] > 0:
        print(f"🏆 Win Rate: {stats['Win Rate [%]']:.1f}%")
        print(f"📈 Best Trade: {stats['Best Trade [%]']:.1f}%")
        print(f"📉 Worst Trade: {stats['Worst Trade [%]']:.1f}%")
    
    if stats['Return [%]'] > stats['Buy & Hold Return [%]'] and stats['# Trades'] >= 5:
        print("\n🏆🌙🚀 BREAKTHROUGH VICTORY! BEATS BUY & HOLD WITH 5+ TRADES! 🚀🌙🏆")
        print("🎉🎉🎉 BREAKTHROUGH MISSION ACCOMPLISHED! 🎉🎉🎉")
        print("🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨🌙🚀✨")
    elif stats['# Trades'] >= 5:
        print(f"\n✅ Achieved 5+ trades ({stats['# Trades']}) with {stats['Return [%]']:.1f}% return")
        if stats['Return [%]'] > 100:
            print("🎊 EXCELLENT! Over 100% returns! 🎊")
        elif stats['Return [%]'] > 50:
            print("🎉 GREAT! Over 50% returns! 🎉")
    else:
        print(f"\n📊 {stats['# Trades']} trades, {stats['Return [%]']:.1f}% return - continuing...")
    
    print("\n🌙 MoonDev BREAKTHROUGH Backtest Complete! 🚀")