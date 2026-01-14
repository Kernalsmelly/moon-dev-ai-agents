# 🌙 Moon Dev's Liquidity Divergence Backtest 🌙
import pandas as pd
from backtesting import Backtest, Strategy

# ========================
# DATA PREPARATION
# ========================
data_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv"

# Load and clean data with Moon Dev standards
data = pd.read_csv(data_path, parse_dates=['datetime'], index_col='datetime')
data.columns = data.columns.str.strip().str.lower()
data = data.drop(columns=[col for col in data.columns if 'unnamed' in col.lower()])

# Map columns to backtesting.py format with lunar precision 🌕
data.rename(columns={
    'open': 'Open',
    'high': 'High',
    'low': 'Low',
    'close': 'Close',
    'volume': 'Volume'
}, inplace=True)

# ========================
# LIQUIDITY STRATEGY 
# ========================
class LiquidityDivergence(Strategy):
    obi_entry = 10  # Bid dominance threshold
    obi_exit = 5    # Liquidity drain level
    risk_pct = 0.02 # Moon-sized position allocation 🌙
    max_bars_held = 96  # 24h in 15m intervals
    
    def init(self):
        # No traditional indicators needed - pure liquidity signals 🌊
        print("🌌 Initializing Moon Dev's Cosmic Liquidity Scanner...")
        print("✨ All systems nominal - backtesting.lib dependencies purged from orbit! 🚀")
        
        # Initialize entry bar tracker
        self.entry_bar = 0
        
    def next(self):
        current_obi = self.data.obi[-1]
        current_funding = self.data.funding_rate[-1]
        price = self.data.Close[-1]

        # 🌙 Lunar Debug Console
        if len(self.data) % 100 == 0:
            print(f"🌕 Moon Phase Update | Bar: {len(self.data)} | OBI: {current_obi:.1f}% | Funding: {current_funding:.4f}")

        # ========================
        # CORE STRATEGY LOGIC
        # ========================
        if not self.position:
            # 🌠 ENTRY: Strong bids + negative funding
            if current_obi > self.obi_entry and current_funding < 0:
                # Calculate position size with cosmic precision 🌌
                position_size = (self.risk_pct * self.equity) / price
                size = int(round(position_size))
                
                if size > 0:  # Ensure valid position size
                    print(f"🚀🌙 BLACK HOLE ENTRY! Size: {size} | Price: {price:.2f}")
                    self.buy(
                        size=size, 
                        sl=price * 0.98,  # 2% stellar stop loss 🌠
                        tag=f"OBI:{current_obi:.1f}% Funding:{current_funding:.4f}"
                    )
                    self.entry_bar = len(self.data)
        else:
            # 🛸 EXIT: Liquidity drain or funding flip
            exit_condition = (
                current_obi < self.obi_exit or 
                current_funding > 0 or
                (len(self.data) - self.entry_bar) >= self.max_bars_held
            )
            
            if exit_condition:
                # Generate exit report for mission control 📡
                reasons = []
                if current_obi < self.obi_exit:
                    reasons.append("🪐 OBI Collapse")
                if current_funding > 0:
                    reasons.append("🌗 Funding Flip")
                if (len(self.data) - self.entry_bar) >= self.max_bars_held:
                    reasons.append("⌛ Time Warp Expired")
                
                print(f"🌑 EXIT SIGNAL! Reasons: {' + '.join(reasons)}")
                self.position.close()

# ========================
# LAUNCH BACKTEST
# ========================
bt = Backtest(data, LiquidityDivergence, cash=1_000_000)
stats = bt.run()
print("\n🌠🌠🌠 FINAL MISSION REPORT 🌠🌠🌠")
print(stats)
print(stats._strategy)