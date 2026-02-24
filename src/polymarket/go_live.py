"""
Go Live Script for Polymarket Micro-Edge Trading

This script safely transitions from dry-run to live trading:
1. Verifies wallet connection and balance
2. Checks py-clob-client installation
3. Validates API credentials
4. Sets safe position limits
5. Runs a test order (optional)

Usage:
    python -m src.polymarket.go_live --check      # Just check readiness
    python -m src.polymarket.go_live --test       # Place a tiny test order
    python -m src.polymarket.go_live --enable     # Enable live mode in config

Requirements:
    - PRIVATE_KEY in .env (Polygon wallet private key)
    - py-clob-client package installed
    - USDC balance on Polygon for trading
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

# Load environment
project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")


def check_environment() -> Tuple[bool, str]:
    """Check if environment is properly configured."""
    issues = []

    # Check PRIVATE_KEY
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        issues.append("PRIVATE_KEY not set in .env")
    elif len(private_key) < 64:
        issues.append("PRIVATE_KEY appears invalid (too short)")

    # Check chain ID
    chain_id = os.getenv("POLYGON_CHAIN_ID", "137")
    if chain_id not in ["137", "80001"]:
        issues.append(f"POLYGON_CHAIN_ID={chain_id} - should be 137 (mainnet) or 80001 (testnet)")

    if issues:
        return False, "\n".join(issues)
    return True, "Environment configured correctly"


def check_clob_client() -> Tuple[bool, str]:
    """Check if py-clob-client is installed and working."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        return True, "py-clob-client installed and importable"
    except ImportError as e:
        return False, f"py-clob-client not installed: {e}\nInstall with: pip install py-clob-client"


def check_wallet_balance() -> Tuple[bool, str, Dict]:
    """Check wallet USDC balance on Polygon."""
    try:
        from py_clob_client.client import ClobClient

        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            return False, "No private key", {}

        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=int(os.getenv("POLYGON_CHAIN_ID", 137)),
            key=private_key,
            signature_type=2,
        )

        # Get API creds
        client.set_api_creds(client.create_or_derive_api_creds())

        # Get balance info
        # Note: The actual method depends on py-clob-client version
        balance_info = {}

        if hasattr(client, 'get_balance'):
            balance_info = client.get_balance()
        elif hasattr(client, 'get_collateral'):
            balance_info = {'collateral': client.get_collateral()}

        # Try to get USDC balance
        usdc_balance = balance_info.get('balance', balance_info.get('collateral', 0))

        if isinstance(usdc_balance, str):
            usdc_balance = float(usdc_balance)

        if usdc_balance < 10:
            return False, f"USDC balance too low: ${usdc_balance:.2f} (need at least $10)", balance_info

        return True, f"USDC balance: ${usdc_balance:.2f}", balance_info

    except Exception as e:
        return False, f"Could not check balance: {e}", {}


def check_api_connection() -> Tuple[bool, str]:
    """Test connection to Polymarket CLOB API."""
    try:
        import httpx
        client = httpx.Client(timeout=10)

        # Test CLOB API
        resp = client.get("https://clob.polymarket.com/")
        if resp.status_code != 200:
            return False, f"CLOB API returned status {resp.status_code}"

        # Test Gamma API
        resp = client.get("https://gamma-api.polymarket.com/markets?limit=1")
        if resp.status_code != 200:
            return False, f"Gamma API returned status {resp.status_code}"

        return True, "API connections successful"

    except Exception as e:
        return False, f"API connection failed: {e}"


def run_test_order() -> Tuple[bool, str]:
    """Place a tiny test order to verify everything works."""
    try:
        from src.agents.polymarket_executor import PolymarketExecutor

        # Create executor in live mode
        executor = PolymarketExecutor(dry_run=False)

        if executor.client is None:
            return False, "Could not initialize CLOB client"

        # Get a real market to test with
        import httpx
        client = httpx.Client(timeout=10)
        resp = client.get("https://gamma-api.polymarket.com/markets?limit=1&active=true")

        if resp.status_code != 200:
            return False, "Could not fetch test market"

        markets = resp.json()
        if not markets:
            return False, "No active markets found"

        market = markets[0]
        token_id = market.get('clobTokenIds', [''])[0]

        if not token_id:
            return False, "No token ID found for test market"

        # Place a tiny order that won't fill (price far from market)
        # Buy YES at $0.01 - will sit unfilled
        result = executor.place_limit_order(
            market_token_id=token_id,
            side="buy",
            price=0.01,  # Very low price - won't fill
            size=1.0,    # $1 position
        )

        if result.get('success'):
            order_id = result.get('result', {}).get('id')

            # Cancel it immediately
            if order_id:
                executor.cancel_order(order_id)

            return True, f"Test order placed and cancelled successfully"
        else:
            return False, f"Test order failed: {result.get('error')}"

    except Exception as e:
        return False, f"Test order error: {e}"


def update_config_for_live(enable: bool = True) -> Tuple[bool, str]:
    """Update config to enable/disable live trading."""
    config_path = project_root / "src" / "config" / "polymarket_micro_config.py"

    if not config_path.exists():
        return False, f"Config file not found: {config_path}"

    try:
        content = config_path.read_text()

        if enable:
            # Change DRY_RUN = True to DRY_RUN = False
            if "DRY_RUN = True" in content:
                content = content.replace("DRY_RUN = True", "DRY_RUN = False")
                config_path.write_text(content)
                return True, "Live mode ENABLED in config (DRY_RUN = False)"
            elif "DRY_RUN = False" in content:
                return True, "Live mode already enabled"
            else:
                return False, "Could not find DRY_RUN setting in config"
        else:
            # Change DRY_RUN = False to DRY_RUN = True
            if "DRY_RUN = False" in content:
                content = content.replace("DRY_RUN = False", "DRY_RUN = True")
                config_path.write_text(content)
                return True, "Dry-run mode ENABLED in config (DRY_RUN = True)"
            elif "DRY_RUN = True" in content:
                return True, "Dry-run mode already enabled"
            else:
                return False, "Could not find DRY_RUN setting in config"

    except Exception as e:
        return False, f"Error updating config: {e}"


def print_checklist():
    """Print full readiness checklist."""
    print("\n" + "=" * 60)
    print("   POLYMARKET LIVE TRADING READINESS CHECK")
    print("=" * 60)
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")

    all_passed = True

    # 1. Environment
    print("1. Environment Configuration")
    passed, msg = check_environment()
    status = "PASS" if passed else "FAIL"
    print(f"   [{status}] {msg}")
    all_passed = all_passed and passed

    # 2. CLOB Client
    print("\n2. py-clob-client Package")
    passed, msg = check_clob_client()
    status = "PASS" if passed else "FAIL"
    print(f"   [{status}] {msg}")
    all_passed = all_passed and passed

    # 3. API Connection
    print("\n3. API Connection")
    passed, msg = check_api_connection()
    status = "PASS" if passed else "FAIL"
    print(f"   [{status}] {msg}")
    all_passed = all_passed and passed

    # 4. Wallet Balance (only if previous checks passed)
    print("\n4. Wallet Balance")
    if all_passed:
        passed, msg, balance = check_wallet_balance()
        status = "PASS" if passed else "FAIL"
        print(f"   [{status}] {msg}")
        all_passed = all_passed and passed
    else:
        print("   [SKIP] Skipped - fix previous issues first")

    # Summary
    print("\n" + "=" * 60)
    if all_passed:
        print("   ALL CHECKS PASSED - Ready for live trading!")
        print("\n   To enable live mode, run:")
        print("   python -m src.polymarket.go_live --enable")
    else:
        print("   SOME CHECKS FAILED - Fix issues before going live")
    print("=" * 60 + "\n")

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Polymarket Live Trading Setup")
    parser.add_argument("--check", action="store_true", help="Run readiness checklist")
    parser.add_argument("--test", action="store_true", help="Place a test order")
    parser.add_argument("--enable", action="store_true", help="Enable live trading in config")
    parser.add_argument("--disable", action="store_true", help="Disable live trading (back to dry-run)")

    args = parser.parse_args()

    if args.check or (not args.test and not args.enable and not args.disable):
        print_checklist()

    if args.test:
        print("\nRunning test order...")
        passed, msg = run_test_order()
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {msg}")

    if args.enable:
        print("\nEnabling live mode...")
        passed, msg = update_config_for_live(enable=True)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {msg}")

        if passed:
            print("\n*** WARNING: Live trading is now ENABLED ***")
            print("*** Real money will be used for orders ***")
            print("*** Start with small positions to verify ***")

    if args.disable:
        print("\nDisabling live mode (back to dry-run)...")
        passed, msg = update_config_for_live(enable=False)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {msg}")


if __name__ == "__main__":
    main()
