# 🌙 Moon Dev's Quick Strategy Parameter Test 🌙
# Quick test to verify parameter changes without full backtesting

import os

def check_strategy_parameters():
    """Check if all strategy files have been updated with correct parameters"""
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    strategies_to_check = [
        {
            'file': 'SimpleMomentumCross_BT.py',
            'expected_changes': ['ema_fast = 8', 'ema_slow = 21'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'RSIMeanReversion_BT.py', 
            'expected_changes': ['rsi_oversold = 35', 'rsi_overbought = 65'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'VolatilityBreakout_BT.py',
            'expected_changes': ['breakout_multiplier = 1.5'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'BollingerReversion_BT.py',
            'expected_changes': ['bb_period = 15'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'MACDDivergence_BT.py',
            'expected_changes': ['divergence_lookback = 15'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'StochasticMomentum_BT.py',
            'expected_changes': ['stoch_oversold = 25', 'stoch_overbought = 75'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'TrendFollowingMA_BT.py',
            'expected_changes': ['ma_fast = 5', 'ma_medium = 10', 'ma_slow = 20'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'VolumeWeightedBreakout_BT.py',
            'expected_changes': ['volume_threshold = 1.5'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'ATRChannelSystem_BT.py',
            'expected_changes': ['channel_multiplier = 1.5'],
            'removed': ['constraint=lambda']
        },
        {
            'file': 'HybridMomentumReversion_BT.py',
            'expected_changes': ['rsi_period = 10'],
            'removed': ['constraint=lambda']
        }
    ]
    
    print("🌙 CHECKING STRATEGY PARAMETER UPDATES")
    print("=" * 80)
    print(f"{'Strategy':<30} {'Parameter Updates':<25} {'Constraint Removed':<20} {'Status'}")
    print("=" * 80)
    
    all_good = True
    
    for strategy in strategies_to_check:
        file_path = os.path.join(current_dir, strategy['file'])
        
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            # Check for expected parameter changes
            params_found = all(change in content for change in strategy['expected_changes'])
            
            # Check that constraint was removed
            constraint_removed = not any(removed in content for removed in strategy['removed'])
            
            # Check for maximize='Sharpe Ratio'
            maximize_added = "maximize='Sharpe Ratio'" in content
            
            if params_found and constraint_removed and maximize_added:
                status = "✅ PASS"
            else:
                status = "❌ FAIL"
                all_good = False
            
            param_status = "✅" if params_found else "❌"
            constraint_status = "✅" if constraint_removed else "❌"
            
            print(f"{strategy['file'][:-3]:<30} {param_status:<25} {constraint_status:<20} {status}")
            
            if not params_found:
                print(f"   Missing: {', '.join(strategy['expected_changes'])}")
            if not constraint_removed:
                print("   Still has constraint parameter")
            if not maximize_added:
                print("   Missing maximize='Sharpe Ratio'")
                
        except FileNotFoundError:
            print(f"{strategy['file'][:-3]:<30} {'❌ FILE NOT FOUND':<25} {'❌':<20} {'❌ ERROR'}")
            all_good = False
        except Exception:
            print(f"{strategy['file'][:-3]:<30} {'❌ ERROR':<25} {'❌':<20} {'❌ ERROR'}")
            all_good = False
    
    print("=" * 80)
    
    if all_good:
        print("\n🏆 ALL STRATEGIES SUCCESSFULLY UPDATED! 🏆")
        print("✅ All parameter changes applied correctly")
        print("✅ All constraint parameters removed")
        print("✅ All maximize='Sharpe Ratio' added")
        print("\n📈 EXPECTED IMPROVEMENTS:")
        print("   • SimpleMomentumCross: More EMA crossovers (8/21 vs 12/26)")
        print("   • RSIMeanReversion: More RSI signals (35/65 vs 30/70)")
        print("   • VolatilityBreakout: More breakouts (1.5x vs 2.0x ATR)")
        print("   • BollingerReversion: More BB touches (15 vs 20 period)")
        print("   • MACDDivergence: More divergences (15 vs 20 lookback)")
        print("   • StochasticMomentum: More signals (25/75 vs 20/80)")
        print("   • TrendFollowingMA: More signals (5/10/20 vs 10/20/50)")
        print("   • VolumeWeightedBreakout: More breakouts (1.5x vs 1.8x volume)")
        print("   • ATRChannelSystem: More channel breaks (1.5x vs 2.0x)")
        print("   • HybridMomentumReversion: More RSI signals (10 vs 14 period)")
    else:
        print("\n⚠️ SOME STRATEGIES NEED ATTENTION")
        print("🔧 Please check the failed strategies above")
    
    return all_good

if __name__ == "__main__":
    check_strategy_parameters()
    print("\n🌙 Quick parameter check completed! ✨")