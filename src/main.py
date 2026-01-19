"""
🌙 Moon Dev's AI Trading System
Main entry point for running trading agents
"""

import os
import sys
from termcolor import cprint
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta
import src.config as config

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# Register global MarketBrain shield early so repo-local AsyncClient imports
# will delegate through the shield. This helps ensure all modules get routed
# through the centralized RPC caller even if they import the local shim.
try:
    from src.brain import MarketBrain
    MarketBrain.register_global_shield()
except Exception:
    pass

# Import agents
from src.agents.trading_agent import TradingAgent  # noqa: E402
from src.agents.risk_agent import RiskAgent  # noqa: E402
from src.agents.strategy_agent import StrategyAgent  # noqa: E402
from src.agents.copybot_agent import CopyBotAgent  # noqa: E402
from src.agents.sentiment_agent import SentimentAgent  # noqa: E402

# Load environment variables
load_dotenv()

# Agent Configuration
ACTIVE_AGENTS = {
    'risk': False,      # Risk management agent
    'trading': False,   # LLM trading agent
    'strategy': False,  # Strategy-based trading agent
    'copybot': False,   # CopyBot agent
    'sentiment': False, # Run sentiment_agent.py directly instead
    # whale_agent is run from whale_agent.py
    # Add more agents here as we build them:
    # 'portfolio': False,  # Future portfolio optimization agent
}

def run_agents():
    """Run all active agents in sequence"""
    try:
        # Initialize active agents
        trading_agent = TradingAgent() if ACTIVE_AGENTS['trading'] else None
        risk_agent = RiskAgent() if ACTIVE_AGENTS['risk'] else None
        strategy_agent = StrategyAgent() if ACTIVE_AGENTS['strategy'] else None
        copybot_agent = CopyBotAgent() if ACTIVE_AGENTS['copybot'] else None
        sentiment_agent = SentimentAgent() if ACTIVE_AGENTS['sentiment'] else None

        while True:
            try:
                # Run Risk Management
                if risk_agent:
                    cprint("\n🛡️ Running Risk Management...", "cyan")
                    risk_agent.run()

                # Run Trading Analysis
                if trading_agent:
                    cprint("\n🤖 Running Trading Analysis...", "cyan")
                    trading_agent.run()

                # Run Strategy Analysis
                if strategy_agent:
                    cprint("\n📊 Running Strategy Analysis...", "cyan")
                    for token in config.MONITORED_TOKENS:
                        if token not in config.EXCLUDED_TOKENS:  # Skip USDC and other excluded tokens
                            cprint(f"\n🔍 Analyzing {token}...", "cyan")
                            strategy_agent.get_signals(token)

                # Run CopyBot Analysis
                if copybot_agent:
                    cprint("\n🤖 Running CopyBot Portfolio Analysis...", "cyan")
                    copybot_agent.run_analysis_cycle()

                # Run Sentiment Analysis
                if sentiment_agent:
                    cprint("\n🎭 Running Sentiment Analysis...", "cyan")
                    sentiment_agent.run()

                # Sleep until next cycle
                next_run = datetime.now() + timedelta(minutes=config.SLEEP_BETWEEN_RUNS_MINUTES)
                cprint(f"\n😴 Sleeping until {next_run.strftime('%H:%M:%S')}", "cyan")
                time.sleep(60 * config.SLEEP_BETWEEN_RUNS_MINUTES)

            except Exception as e:
                cprint(f"\n❌ Error running agents: {str(e)}", "red")
                cprint("🔄 Continuing to next cycle...", "yellow")
                time.sleep(60)  # Sleep for 1 minute on error before retrying

    except KeyboardInterrupt:
        cprint("\n👋 Gracefully shutting down...", "yellow")
    except Exception as e:
        cprint(f"\n❌ Fatal error in main loop: {str(e)}", "red")
        raise

if __name__ == "__main__":
    cprint("\n🌙 Moon Dev AI Agent Trading System Starting...", "white", "on_blue")
    cprint("\n📊 Active Agents:", "white", "on_blue")
    for agent, active in ACTIVE_AGENTS.items():
        status = "✅ ON" if active else "❌ OFF"
        cprint(f"  • {agent.title()}: {status}", "white", "on_blue")
    print("\n")

    # Security first: warn when in shadow mode so operators know no real SOL will be spent
    try:
        if getattr(config, 'SHADOW_MODE', True):
            cprint("[SECURITY] Shadow Mode Active - No real SOL will be spent.", "yellow")
            # ensure live exits are disabled unless explicitly overridden
            if os.getenv('ENABLE_LIVE_EXITS', str(getattr(config, 'ENABLE_LIVE_EXITS', '0'))).lower() in ('1', 'true', 'yes'):
                cprint("[WARNING] ENABLE_LIVE_EXITS is set but SHADOW_MODE is active — live sends will be suppressed.", "red")
    except Exception:
        pass

    run_agents()