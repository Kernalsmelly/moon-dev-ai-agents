# 🌙 Moon Dev's Final Winning Strategies Test Suite 🌙
# Comprehensive testing of the 3 FINAL strategies designed to achieve both:
# - More than 100 trades
# - Sharpe ratio of 2.0 or better

import pandas as pd
import numpy as np
from backtesting import Backtest
import warnings
from datetime import datetime
import importlib.util

warnings.filterwarnings('ignore')

# 🌙 Data Loading Function (Standardized)
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

def load_strategy_class(file_path, class_name):
    """Dynamically load strategy class from file"""
    try:
        spec = importlib.util.spec_from_file_location("strategy_module", file_path)
        strategy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(strategy_module)
        return getattr(strategy_module, class_name)
    except Exception as e:
        print(f"Error loading strategy {class_name} from {file_path}: {e}")
        return None

def test_single_strategy(strategy_class, strategy_name, data, run_optimization=True):
    """Test a single strategy with detailed analysis"""
    print(f"\n🌙 Testing {strategy_name}...")
    print("=" * 70)
    
    try:
        # Run default backtest
        bt = Backtest(data, strategy_class, cash=1000000, commission=.002)
        
        print(f"🔄 Running default backtest for {strategy_name}...")
        stats_default = bt.run()
        
        # Store default results
        default_results = {
            'strategy': strategy_name,
            'type': 'Default',
            'trades': stats_default['# Trades'],
            'return_pct': stats_default['Return [%]'],
            'sharpe_ratio': stats_default['Sharpe Ratio'],
            'max_drawdown': stats_default['Max. Drawdown [%]'],
            'win_rate': stats_default['Win Rate [%]'],
            'trade_requirement': stats_default['# Trades'] > 100,
            'sharpe_requirement': stats_default['Sharpe Ratio'] > 2.0,
            'overall_success': stats_default['# Trades'] > 100 and stats_default['Sharpe Ratio'] > 2.0
        }
        
        print("📊 Default Results:")
        print(f"   Trades: {default_results['trades']}")
        print(f"   Return: {default_results['return_pct']:.2f}%")
        print(f"   Sharpe: {default_results['sharpe_ratio']:.2f}")
        print(f"   Max DD: {default_results['max_drawdown']:.2f}%")
        print(f"   Win Rate: {default_results['win_rate']:.2f}%")
        
        # Run optimization if requested
        optimized_results = None
        if run_optimization:
            try:
                print(f"🔄 Running optimization for {strategy_name}...")
                
                # Define optimization parameters based on strategy type
                if "Divergence" in strategy_name:
                    stats_opt = bt.optimize(
                        swing_period=range(10, 16, 2),
                        min_separation=range(6, 12, 2),
                        atr_multiplier=[1.8, 2.0, 2.2, 2.5],
                        volume_spike_threshold=[1.5, 1.8, 2.0],
                        trailing_start=[1.0, 1.5, 2.0],
                        maximize='Sharpe Ratio',
                        constraint=lambda p: p['# Trades'] > 100 and p['Return [%]'] > -50
                    )
                elif "Momentum" in strategy_name:
                    stats_opt = bt.optimize(
                        ema_fast=range(10, 16, 2),
                        ema_slow=range(24, 30, 2),
                        pullback_threshold=[1.5, 2.0, 2.5],
                        volume_multiplier=[1.3, 1.5, 1.8],
                        atr_stop_multiplier=[2.0, 2.5, 3.0],
                        take_profit_ratio=[2.5, 3.0, 3.5],
                        maximize='Sharpe Ratio',
                        constraint=lambda p: p['# Trades'] > 100 and p['Return [%]'] > -50
                    )
                elif "Trend" in strategy_name:
                    stats_opt = bt.optimize(
                        trend_ema_fast=range(12, 16, 1),
                        trend_ema_slow=range(32, 38, 2),
                        adx_threshold=range(20, 30, 3),
                        initial_stop_atr=[1.8, 2.0, 2.2],
                        profit_target_atr=[3.5, 4.0, 4.5],
                        volume_threshold=[1.2, 1.4, 1.6],
                        maximize='Sharpe Ratio',
                        constraint=lambda p: p['# Trades'] > 100 and p['Return [%]'] > -50
                    )
                else:
                    # Generic optimization
                    stats_opt = bt.optimize(
                        maximize='Sharpe Ratio',
                        constraint=lambda p: p['# Trades'] > 100 and p['Return [%]'] > -50
                    )
                
                optimized_results = {
                    'strategy': strategy_name,
                    'type': 'Optimized',
                    'trades': stats_opt['# Trades'],
                    'return_pct': stats_opt['Return [%]'],
                    'sharpe_ratio': stats_opt['Sharpe Ratio'],
                    'max_drawdown': stats_opt['Max. Drawdown [%]'],
                    'win_rate': stats_opt['Win Rate [%]'],
                    'trade_requirement': stats_opt['# Trades'] > 100,
                    'sharpe_requirement': stats_opt['Sharpe Ratio'] > 2.0,
                    'overall_success': stats_opt['# Trades'] > 100 and stats_opt['Sharpe Ratio'] > 2.0
                }
                
                print("📊 Optimized Results:")
                print(f"   Trades: {optimized_results['trades']}")
                print(f"   Return: {optimized_results['return_pct']:.2f}%")
                print(f"   Sharpe: {optimized_results['sharpe_ratio']:.2f}")
                print(f"   Max DD: {optimized_results['max_drawdown']:.2f}%")
                print(f"   Win Rate: {optimized_results['win_rate']:.2f}%")
                
            except Exception as e:
                print(f"⚠️ Optimization failed for {strategy_name}: {e}")
                optimized_results = None
        
        # Validation summary
        best_results = optimized_results if optimized_results else default_results
        
        print("\n✅ VALIDATION SUMMARY:")
        print(f"📊 Trade Count (>100): {'✅ PASS' if best_results['trade_requirement'] else '❌ FAIL'} ({best_results['trades']} trades)")
        print(f"📈 Sharpe Ratio (>2.0): {'✅ PASS' if best_results['sharpe_requirement'] else '❌ FAIL'} ({best_results['sharpe_ratio']:.2f})")
        print(f"🏆 Overall Success: {'✅ SUCCESS' if best_results['overall_success'] else '❌ NEEDS WORK'}")
        
        return default_results, optimized_results
        
    except Exception as e:
        print(f"❌ Error testing {strategy_name}: {e}")
        return None, None

def run_comprehensive_test_suite():
    """Run comprehensive test suite for all final strategies"""
    print("🌙" + "="*78 + "🌙")
    print("                     MOON DEV'S FINAL WINNING STRATEGIES")
    print("                        COMPREHENSIVE TEST SUITE")
    print("🌙" + "="*78 + "🌙")
    print(f"📅 Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("🎯 Target: 100+ trades AND 2.0+ Sharpe ratio")
    
    # Load data
    print("\n📊 Loading BTC-USD 15m data...")
    data = load_btc_data(data_path)
    print(f"✅ Data loaded: {len(data)} bars from {data.index[0]} to {data.index[-1]}")
    
    # Strategy configurations
    base_path = "/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/FINAL_WINNING_STRATEGIES"
    strategies = [
        {
            'name': 'DivergenceVolatilityEnhanced',
            'file': f'{base_path}/DivergenceVolatilityEnhanced_BT.py',
            'class': 'DivergenceVolatilityEnhanced',
            'description': 'Enhanced divergence detection with multi-confirmation system'
        },
        {
            'name': 'SelectiveMomentumSwing',
            'file': f'{base_path}/SelectiveMomentumSwing_BT.py',
            'class': 'SelectiveMomentumSwing',
            'description': 'High-probability momentum swings with selective entries'
        },
        {
            'name': 'TrendCapturePro',
            'file': f'{base_path}/TrendCapturePro_BT.py',
            'class': 'TrendCapturePro',
            'description': 'Professional trend capture with advanced trailing stops'
        }
    ]
    
    # Test all strategies
    all_results = []
    successful_strategies = []
    
    for strategy_config in strategies:
        print(f"\n🚀 TESTING STRATEGY: {strategy_config['name']}")
        print(f"📋 Description: {strategy_config['description']}")
        
        # Load strategy class
        strategy_class = load_strategy_class(strategy_config['file'], strategy_config['class'])
        
        if strategy_class is None:
            print(f"❌ Failed to load {strategy_config['name']}")
            continue
            
        # Test strategy
        default_result, optimized_result = test_single_strategy(
            strategy_class, 
            strategy_config['name'], 
            data, 
            run_optimization=True
        )
        
        if default_result:
            all_results.append(default_result)
            
        if optimized_result:
            all_results.append(optimized_result)
            
            # Check if strategy meets both requirements
            if optimized_result['overall_success']:
                successful_strategies.append(optimized_result)
                print(f"🏆 {strategy_config['name']} MEETS BOTH REQUIREMENTS!")
            else:
                print(f"⚠️ {strategy_config['name']} needs further optimization")
    
    # Generate comprehensive summary report
    print("\n🌙 FINAL COMPREHENSIVE RESULTS SUMMARY")
    print("=" * 80)
    
    print("\n📈 OVERALL PERFORMANCE:")
    print(f"✅ Strategies Tested: {len(strategies)}")
    print(f"🏆 Successful Strategies: {len(successful_strategies)}/3")
    print(f"📊 Total Backtests Run: {len(all_results)}")
    
    if successful_strategies:
        print("\n🏆 SUCCESSFUL STRATEGIES (Meeting Both Requirements):")
        print("-" * 80)
        print(f"{'Strategy':<25} {'Trades':<8} {'Return%':<9} {'Sharpe':<8} {'DrawDn%':<9} {'WinRate%':<9}")
        print("-" * 80)
        
        for result in successful_strategies:
            print(f"{result['strategy']:<25} "
                  f"{result['trades']:<8} "
                  f"{result['return_pct']:<9.2f} "
                  f"{result['sharpe_ratio']:<8.2f} "
                  f"{result['max_drawdown']:<9.2f} "
                  f"{result['win_rate']:<9.2f}")
        print("-" * 80)
    
    # Detailed comparison table
    print("\n📊 DETAILED STRATEGY COMPARISON:")
    print("-" * 90)
    print(f"{'Strategy':<25} {'Type':<10} {'Trades':<8} {'Return%':<9} {'Sharpe':<8} {'MaxDD%':<8} {'Success':<8}")
    print("-" * 90)
    
    for result in all_results:
        success_symbol = '✅' if result['overall_success'] else '❌'
        print(f"{result['strategy']:<25} "
              f"{result['type']:<10} "
              f"{result['trades']:<8} "
              f"{result['return_pct']:<9.2f} "
              f"{result['sharpe_ratio']:<8.2f} "
              f"{result['max_drawdown']:<8.2f} "
              f"{success_symbol:<8}")
    print("-" * 90)
    
    # Requirements analysis
    print("\n📋 REQUIREMENTS ANALYSIS:")
    trade_passers = [r for r in all_results if r['trade_requirement']]
    sharpe_passers = [r for r in all_results if r['sharpe_requirement']]
    
    print(f"📊 Trade Count Requirement (>100): {len(trade_passers)}/{len(all_results)} strategies")
    print(f"📈 Sharpe Ratio Requirement (>2.0): {len(sharpe_passers)}/{len(all_results)} strategies")
    print(f"🏆 Both Requirements Met: {len(successful_strategies)}/{len(all_results)} strategies")
    
    # Performance insights
    if all_results:
        avg_trades = np.mean([r['trades'] for r in all_results])
        avg_sharpe = np.mean([r['sharpe_ratio'] for r in all_results if not np.isnan(r['sharpe_ratio'])])
        avg_return = np.mean([r['return_pct'] for r in all_results])
        
        print("\n📊 AVERAGE PERFORMANCE METRICS:")
        print(f"   📊 Average Trades: {avg_trades:.0f}")
        print(f"   📈 Average Sharpe Ratio: {avg_sharpe:.2f}")
        print(f"   💰 Average Return: {avg_return:.2f}%")
    
    # Final assessment
    print("\n🎯 FINAL MISSION ASSESSMENT:")
    success_rate = len(successful_strategies) / len(strategies) * 100 if strategies else 0
    
    if len(successful_strategies) == 3:
        print("🏆 MISSION ACCOMPLISHED! ALL 3 STRATEGIES SUCCESSFUL!")
        print("   ✅ 100% success rate achieving both requirements")
        print("   ✅ Ready for production deployment")
        print("   ✅ Exceptional risk-adjusted returns demonstrated")
    elif len(successful_strategies) >= 2:
        print("🌟 MISSION LARGELY SUCCESSFUL! Multiple winning strategies!")
        print(f"   ✅ {success_rate:.0f}% success rate")
        print("   ✅ Strong foundation for trading system")
        print("   ⚠️ Consider further optimization of remaining strategies")
    elif len(successful_strategies) >= 1:
        print("⭐ PARTIAL SUCCESS! At least one winning strategy!")
        print(f"   ✅ {success_rate:.0f}% success rate")
        print("   ✅ Proof of concept achieved")
        print("   🔄 Significant optimization needed for other strategies")
    else:
        print("❌ MISSION REQUIRES CONTINUATION!")
        print("   ❌ No strategies currently meet both requirements")
        print("   🔄 Fundamental strategy redesign may be needed")
        print("   📊 Consider different approaches or market conditions")
    
    # Recommendations
    print("\n💡 STRATEGIC RECOMMENDATIONS:")
    if successful_strategies:
        best_strategy = max(successful_strategies, key=lambda x: x['sharpe_ratio'])
        print(f"🏆 Best Performing Strategy: {best_strategy['strategy']}")
        print(f"   📈 Sharpe Ratio: {best_strategy['sharpe_ratio']:.2f}")
        print(f"   📊 Trade Count: {best_strategy['trades']}")
        print(f"   💰 Return: {best_strategy['return_pct']:.2f}%")
        
        print("\n🚀 DEPLOYMENT RECOMMENDATIONS:")
        print("   1. Implement successful strategies in paper trading")
        print("   2. Monitor performance in different market conditions")
        print("   3. Consider portfolio diversification with multiple strategies")
        print("   4. Implement robust risk management systems")
        print("   5. Regular performance monitoring and reoptimization")
    
    print("\n🌙 Final Winning Strategies Test Suite completed! ✨")
    print("📊 Total Runtime: Testing completed successfully")
    
    return all_results, successful_strategies

if __name__ == "__main__":
    # Run the comprehensive test suite
    print("🌙 Initializing Final Winning Strategies Test Suite...")
    results, winners = run_comprehensive_test_suite()
    
    # Export results summary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_file = f"/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/rbi/FINAL_WINNING_STRATEGIES/test_results_{timestamp}.txt"
    
    try:
        with open(summary_file, 'w') as f:
            f.write("🌙 MOON DEV'S FINAL WINNING STRATEGIES - TEST RESULTS 🌙\n")
            f.write("=" * 60 + "\n")
            f.write(f"Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Successful Strategies: {len(winners)}/3\n\n")
            
            if winners:
                f.write("SUCCESSFUL STRATEGIES:\n")
                for winner in winners:
                    f.write(f"- {winner['strategy']}: {winner['trades']} trades, {winner['sharpe_ratio']:.2f} Sharpe\n")
            
        print(f"📄 Results summary exported to: {summary_file}")
        
    except Exception as e:
        print(f"⚠️ Could not export results summary: {e}")