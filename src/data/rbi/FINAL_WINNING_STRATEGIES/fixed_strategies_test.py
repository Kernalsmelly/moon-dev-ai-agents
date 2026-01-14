# 🌙 Moon Dev's Fixed Strategies Test Runner 🌙
# Test runner to verify all 10 fixed strategies generate 25-100 trades
# Updated parameters for increased trade frequency

import pandas as pd
from backtesting import Backtest
import warnings
warnings.filterwarnings('ignore')

# Import all strategy classes
import sys
import os

# Add current directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# Import strategy modules
try:
    from SimpleMomentumCross_BT import SimpleMomentumCross
    from RSIMeanReversion_BT import RSIMeanReversion  
    from VolatilityBreakout_BT import VolatilityBreakout
    from BollingerReversion_BT import BollingerReversion
    from MACDDivergence_BT import MACDDivergence
    from StochasticMomentum_BT import StochasticMomentum
    from TrendFollowingMA_BT import TrendFollowingMA
    from VolumeWeightedBreakout_BT import VolumeWeightedBreakout
    from ATRChannelSystem_BT import ATRChannelSystem
    from HybridMomentumReversion_BT import HybridMomentumReversion
except ImportError as e:
    print(f"Import error: {e}")
    print("Running individual strategy files instead...")

# 🌙 Data Loading with Adaptive Header Detection
data_path = '/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/BTC-USD-15m.csv'

def load_btc_data(file_path):
    """Load and prepare BTC data with adaptive header detection"""
    try:
        # Try reading with header first
        df = pd.read_csv(file_path)
        
        # Clean column names
        df.columns = df.columns.str.strip().str.lower()
        
        # Remove unnamed columns
        df = df.drop(columns=[col for col in df.columns if 'unnamed' in col.lower()], errors='ignore')
        
        # Standardize column names
        column_mapping = {
            'datetime': 'datetime',
            'timestamp': 'datetime', 
            'date': 'datetime',
            'time': 'datetime',
            'open': 'Open',
            'high': 'High',
            'low': 'Low', 
            'close': 'Close',
            'volume': 'Volume'
        }
        
        for old_col, new_col in column_mapping.items():
            if old_col in df.columns:
                df = df.rename(columns={old_col: new_col})
        
        # Convert datetime and set as index
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df = df.set_index('datetime')
        
        # Ensure OHLCV columns exist
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Required column {col} not found")
        
        # Clean data
        df = df[required_cols].dropna()
        df = df[df > 0]  # Remove zero/negative values
        
        return df
        
    except Exception as e:
        print(f"Error loading data: {e}")
        raise

# Load the data
print("🌙 Loading BTC data...")
data = load_btc_data(data_path)
print(f"✅ Data loaded: {len(data)} rows")

# Strategy configurations with updated parameters
strategies = [
    {
        'name': 'SimpleMomentumCross',
        'class': SimpleMomentumCross,
        'target_trades': '25-100',
        'changes': 'EMA periods: 12/26 → 8/21'
    },
    {
        'name': 'RSIMeanReversion', 
        'class': RSIMeanReversion,
        'target_trades': '50-150',
        'changes': 'RSI thresholds: 30/70 → 35/65'
    },
    {
        'name': 'VolatilityBreakout',
        'class': VolatilityBreakout, 
        'target_trades': '40-120',
        'changes': 'ATR multiplier: 2.0 → 1.5'
    },
    {
        'name': 'BollingerReversion',
        'class': BollingerReversion,
        'target_trades': '60-180',
        'changes': 'BB period: 20 → 15'
    },
    {
        'name': 'MACDDivergence',
        'class': MACDDivergence,
        'target_trades': '30-80',
        'changes': 'Swing period: 20 → 15'
    },
    {
        'name': 'StochasticMomentum',
        'class': StochasticMomentum,
        'target_trades': '50-150',
        'changes': 'Stochastic thresholds: 20/80 → 25/75'
    },
    {
        'name': 'TrendFollowingMA',
        'class': TrendFollowingMA,
        'target_trades': '40-120',
        'changes': 'MA periods: 10/20/50 → 5/10/20'
    },
    {
        'name': 'VolumeWeightedBreakout',
        'class': VolumeWeightedBreakout,
        'target_trades': '30-90',
        'changes': 'Volume multiplier: 1.8 → 1.5'
    },
    {
        'name': 'ATRChannelSystem',
        'class': ATRChannelSystem,
        'target_trades': '35-100',
        'changes': 'Channel multiplier: 2.0 → 1.5'
    },
    {
        'name': 'HybridMomentumReversion',
        'class': HybridMomentumReversion,
        'target_trades': '50-150',
        'changes': 'RSI period: 14 → 10'
    }
]

# Test results storage
results = []

print("\n🌙 TESTING FIXED STRATEGIES")
print("=" * 80)
print(f"{'Strategy':<25} {'Trades':<8} {'Return%':<10} {'Sharpe':<8} {'Status':<15} {'Changes'}")
print("=" * 80)

for strategy_config in strategies:
    try:
        strategy_name = strategy_config['name']
        strategy_class = strategy_config['class']
        target_trades = strategy_config['target_trades']
        changes = strategy_config['changes']
        
        # Run backtest with fixed parameters
        bt = Backtest(data, strategy_class, cash=1000000, commission=.002)
        stats = bt.run()
        
        # Extract key metrics
        trades = stats['# Trades']
        returns = stats['Return [%]']
        sharpe = stats['Sharpe Ratio']
        
        # Determine status
        if trades >= 25 and trades <= 100:
            status = "✅ PASS"
        elif trades < 25:
            status = "❌ TOO FEW"
        else:
            status = "⚠️ TOO MANY"
        
        # Store results
        results.append({
            'strategy': strategy_name,
            'trades': trades,
            'returns': returns,
            'sharpe': sharpe,
            'status': status,
            'changes': changes
        })
        
        print(f"{strategy_name:<25} {trades:<8} {returns:<10.2f} {sharpe:<8.2f} {status:<15} {changes}")
        
    except Exception as e:
        print(f"{strategy_name:<25} ERROR    -         -        ❌ FAILED      {str(e)[:30]}...")
        results.append({
            'strategy': strategy_name,
            'trades': 0,
            'returns': 0,
            'sharpe': 0,
            'status': 'ERROR',
            'changes': changes
        })

print("=" * 80)

# Summary statistics
total_strategies = len(strategies)
passing_strategies = sum(1 for r in results if r['status'] == '✅ PASS')
failing_strategies = sum(1 for r in results if r['status'] in ['❌ TOO FEW', '⚠️ TOO MANY'])
error_strategies = sum(1 for r in results if r['status'] == 'ERROR')

print("\n📊 SUMMARY RESULTS:")
print(f"📈 Total Strategies Tested: {total_strategies}")
print(f"✅ Passing (25-100 trades): {passing_strategies}")
print(f"❌ Failing (outside range): {failing_strategies}")  
print(f"🚨 Errors: {error_strategies}")

if passing_strategies == total_strategies:
    print("\n🏆 ALL STRATEGIES FIXED SUCCESSFULLY! 🏆")
    print("🌟 All strategies now generate 25-100 trades")
elif passing_strategies > total_strategies // 2:
    print(f"\n✅ MAJORITY FIXED ({passing_strategies}/{total_strategies})")
    print("🔧 Some strategies may need further adjustment")
else:
    print(f"\n⚠️ MORE WORK NEEDED ({passing_strategies}/{total_strategies} passing)")
    print("🔧 Several strategies require additional parameter tuning")

print("\n💡 FIXES APPLIED:")
for strategy_config in strategies:
    print(f"   • {strategy_config['name']}: {strategy_config['changes']}")

print("\n✅ OPTIMIZATION IMPROVEMENTS:")
print("   • Removed constraint parameter from all bt.optimize() calls")
print("   • Added maximize='Sharpe Ratio' to all optimizations")
print("   • Adjusted parameter ranges for better trade generation")

print("\n🌙 Fixed strategies test completed! ✨")