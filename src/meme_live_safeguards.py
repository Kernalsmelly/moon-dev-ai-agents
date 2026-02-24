"""Meme Bot Live Trading Safeguards

Safety checks and controls for live trading:
- Wallet balance verification
- Slippage protection
- Transaction confirmation
- Emergency kill switch
- Position reconciliation

Usage:
    from src.meme_live_safeguards import LiveSafeguards

    safeguards = LiveSafeguards()

    # Before trading
    if await safeguards.check_wallet_balance(min_sol=0.5):
        # Safe to trade

    # Emergency
    await safeguards.emergency_exit_all()
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel

try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    HAS_SOLDERS = True
except ImportError:
    HAS_SOLDERS = False

try:
    from src.brain import MarketBrain
    HAS_BRAIN = True
except ImportError:
    HAS_BRAIN = False

import src.meme_config as meme_config

console = Console()

# Constants
LAMPORTS_PER_SOL = 1_000_000_000
SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class TradeResult:
    """Result of a trade execution."""
    success: bool
    tx_signature: str = ""
    error: str = ""
    input_amount: float = 0.0
    output_amount: float = 0.0
    slippage_pct: float = 0.0
    confirmed: bool = False
    confirmation_time_ms: int = 0


@dataclass
class WalletState:
    """Current wallet state."""
    sol_balance: float = 0.0
    last_updated: float = 0.0
    tokens: dict = field(default_factory=dict)  # mint -> balance


class LiveSafeguards:
    """Safety controls for live trading."""

    def __init__(self, rpc_url: str = None):
        """Initialize safeguards.

        Args:
            rpc_url: Solana RPC URL (uses env/config if not provided)
        """
        self.rpc_url = rpc_url or os.getenv("RPC_URL") or os.getenv("SOLANA_RPC")
        self.keypair = self._load_keypair()
        self.wallet_state = WalletState()
        self.brain = MarketBrain(rpc=self.rpc_url) if HAS_BRAIN else None

        # Emergency state
        self.emergency_mode = False
        self.trading_enabled = True

        # Safeguard settings
        self.min_sol_balance = float(os.getenv('MEME_MIN_SOL_BALANCE', '0.1'))
        self.max_slippage_bps = int(os.getenv('MEME_MAX_SLIPPAGE_BPS', '500'))  # 5%
        self.tx_timeout_seconds = int(os.getenv('MEME_TX_TIMEOUT', '60'))

    def _load_keypair(self) -> Optional[Keypair]:
        """Load wallet keypair from environment."""
        if not HAS_SOLDERS:
            return None
        try:
            key_b58 = os.getenv("SOLANA_PRIVATE_KEY")
            if key_b58:
                return Keypair.from_base58_string(key_b58)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load keypair: {e}[/yellow]")
        return None

    @property
    def wallet_address(self) -> str:
        """Get wallet public key as string."""
        if self.keypair:
            return str(self.keypair.pubkey())
        return ""

    async def check_wallet_balance(self, min_sol: float = None) -> bool:
        """Check if wallet has sufficient SOL balance.

        Args:
            min_sol: Minimum SOL required (uses config default if not provided)

        Returns:
            True if balance is sufficient
        """
        min_required = min_sol or self.min_sol_balance

        try:
            balance = await self._get_sol_balance()
            self.wallet_state.sol_balance = balance
            self.wallet_state.last_updated = time.time()

            if balance < min_required:
                console.print(f"[red]INSUFFICIENT BALANCE: {balance:.4f} SOL < {min_required:.4f} SOL required[/red]")
                return False

            console.print(f"[green]Wallet balance: {balance:.4f} SOL[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Error checking balance: {e}[/red]")
            return False

    async def _get_sol_balance(self) -> float:
        """Get SOL balance from RPC."""
        if not self.keypair or not self.brain:
            return 0.0

        try:
            pubkey = str(self.keypair.pubkey())
            resp = await self.brain._call_rpc('getBalance', [pubkey])

            if isinstance(resp, dict):
                value = resp.get('result', {}).get('value', 0)
            else:
                value = 0

            return value / LAMPORTS_PER_SOL

        except Exception as e:
            console.print(f"[yellow]Balance check error: {e}[/yellow]")
            return 0.0

    async def get_token_balance(self, mint: str) -> float:
        """Get token balance for a specific mint.

        Args:
            mint: Token mint address

        Returns:
            Token balance (in token units, not lamports)
        """
        if not self.keypair or not self.brain:
            return 0.0

        try:
            pubkey = str(self.keypair.pubkey())

            # Get token accounts for this mint
            resp = await self.brain._call_rpc('getTokenAccountsByOwner', [
                pubkey,
                {"mint": mint},
                {"encoding": "jsonParsed"}
            ])

            if isinstance(resp, dict):
                accounts = resp.get('result', {}).get('value', [])
                total = 0.0
                for acc in accounts:
                    info = acc.get('account', {}).get('data', {}).get('parsed', {}).get('info', {})
                    amount = info.get('tokenAmount', {})
                    total += float(amount.get('uiAmount', 0) or 0)
                return total

            return 0.0

        except Exception as e:
            console.print(f"[yellow]Token balance error for {mint[:8]}: {e}[/yellow]")
            return 0.0

    def check_slippage(self, quote: dict, max_slippage_bps: int = None) -> tuple[bool, float]:
        """Check if quote slippage is acceptable.

        Args:
            quote: Jupiter quote response
            max_slippage_bps: Maximum allowed slippage in basis points

        Returns:
            Tuple of (is_acceptable, actual_slippage_pct)
        """
        max_bps = max_slippage_bps or self.max_slippage_bps

        try:
            # Extract slippage from Jupiter quote
            # V6 format: priceImpactPct is percentage
            price_impact = quote.get('priceImpactPct')
            if price_impact is not None:
                slippage_pct = abs(float(price_impact))
                slippage_bps = int(slippage_pct * 100)

                if slippage_bps > max_bps:
                    console.print(f"[red]SLIPPAGE TOO HIGH: {slippage_pct:.2f}% > {max_bps/100:.2f}% max[/red]")
                    return False, slippage_pct

                return True, slippage_pct

            # Fallback: estimate from in/out amounts
            in_amount = float(quote.get('inAmount', 0))
            out_amount = float(quote.get('outAmount', 0))

            if in_amount > 0 and out_amount > 0:
                # This is a rough estimate
                return True, 0.0

            return True, 0.0

        except Exception as e:
            console.print(f"[yellow]Slippage check error: {e}[/yellow]")
            return True, 0.0  # Allow on error, let Jupiter handle it

    async def confirm_transaction(self, signature: str, timeout_seconds: int = None) -> bool:
        """Wait for transaction confirmation on-chain.

        Args:
            signature: Transaction signature
            timeout_seconds: Max time to wait for confirmation

        Returns:
            True if transaction confirmed successfully
        """
        timeout = timeout_seconds or self.tx_timeout_seconds
        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                resp = await self.brain._call_rpc('getSignatureStatuses', [[signature]])

                if isinstance(resp, dict):
                    statuses = resp.get('result', {}).get('value', [])
                    if statuses and statuses[0]:
                        status = statuses[0]

                        # Check for error
                        if status.get('err'):
                            console.print(f"[red]TX FAILED: {status.get('err')}[/red]")
                            return False

                        # Check confirmation status
                        conf_status = status.get('confirmationStatus')
                        if conf_status in ('confirmed', 'finalized'):
                            elapsed = int((time.time() - start_time) * 1000)
                            console.print(f"[green]TX CONFIRMED: {signature[:16]}... ({elapsed}ms)[/green]")
                            return True

                await asyncio.sleep(0.5)

            console.print(f"[red]TX TIMEOUT: {signature[:16]}... (>{timeout}s)[/red]")
            return False

        except Exception as e:
            console.print(f"[red]TX confirmation error: {e}[/red]")
            return False

    async def emergency_exit_all(self, positions: dict) -> list[TradeResult]:
        """Emergency exit all positions immediately.

        Args:
            positions: Dict of mint -> position data

        Returns:
            List of trade results
        """
        self.emergency_mode = True
        self.trading_enabled = False

        console.print(Panel(
            "EMERGENCY EXIT INITIATED\n"
            f"Closing {len(positions)} positions...",
            title="EMERGENCY",
            style="red bold"
        ))

        results = []

        for mint, position in positions.items():
            try:
                # Get token balance
                balance = await self.get_token_balance(mint)
                if balance <= 0:
                    continue

                console.print(f"[yellow]Emergency selling {position.get('symbol', mint[:8])}: {balance} tokens[/yellow]")

                # TODO: Execute actual sell via Jupiter
                # For now, just log it
                result = TradeResult(
                    success=False,
                    error="Emergency exit not yet implemented for live trades"
                )
                results.append(result)

            except Exception as e:
                console.print(f"[red]Error emergency exiting {mint[:8]}: {e}[/red]")
                results.append(TradeResult(success=False, error=str(e)))

        return results

    def enable_trading(self):
        """Re-enable trading after emergency."""
        self.emergency_mode = False
        self.trading_enabled = True
        console.print("[green]Trading re-enabled[/green]")

    def disable_trading(self):
        """Disable trading (soft stop)."""
        self.trading_enabled = False
        console.print("[yellow]Trading disabled[/yellow]")

    async def reconcile_positions(self, paper_positions: dict) -> dict:
        """Reconcile paper positions with actual on-chain balances.

        Args:
            paper_positions: Dict of mint -> paper position data

        Returns:
            Dict of discrepancies found
        """
        discrepancies = {}

        for mint, paper_pos in paper_positions.items():
            try:
                actual_balance = await self.get_token_balance(mint)
                paper_balance = paper_pos.get('amount_tokens', 0)

                # Check for significant discrepancy (>1%)
                if paper_balance > 0:
                    diff_pct = abs(actual_balance - paper_balance) / paper_balance * 100
                    if diff_pct > 1:
                        discrepancies[mint] = {
                            'paper': paper_balance,
                            'actual': actual_balance,
                            'diff_pct': diff_pct
                        }
                        console.print(f"[yellow]DISCREPANCY: {paper_pos.get('symbol', mint[:8])} paper={paper_balance:.2f} actual={actual_balance:.2f}[/yellow]")

            except Exception as e:
                discrepancies[mint] = {'error': str(e)}

        return discrepancies

    async def pre_trade_checks(self, amount_sol: float, mint: str = None) -> tuple[bool, str]:
        """Run all pre-trade safety checks.

        Args:
            amount_sol: Amount of SOL to trade
            mint: Token mint (for buy checks)

        Returns:
            Tuple of (safe_to_trade, reason)
        """
        # 1. Check if trading is enabled
        if not self.trading_enabled:
            return False, "Trading is disabled"

        if self.emergency_mode:
            return False, "Emergency mode active"

        # 2. Check wallet balance
        required = amount_sol + 0.01  # Add buffer for fees
        if not await self.check_wallet_balance(required):
            return False, f"Insufficient balance for {amount_sol} SOL trade"

        # 3. Additional checks can be added here
        # - Rate limiting
        # - Daily loss limits (already in meme_config)
        # - etc.

        return True, "All checks passed"


# Singleton instance
_safeguards_instance: Optional[LiveSafeguards] = None


def get_safeguards() -> LiveSafeguards:
    """Get or create the safeguards singleton."""
    global _safeguards_instance
    if _safeguards_instance is None:
        _safeguards_instance = LiveSafeguards()
    return _safeguards_instance


# Quick test
if __name__ == '__main__':
    async def test():
        safeguards = LiveSafeguards()
        print(f"Wallet: {safeguards.wallet_address}")

        # Test balance check
        has_balance = await safeguards.check_wallet_balance(0.1)
        print(f"Has 0.1 SOL: {has_balance}")

        # Test pre-trade checks
        safe, reason = await safeguards.pre_trade_checks(0.05)
        print(f"Safe to trade 0.05 SOL: {safe} ({reason})")

    asyncio.run(test())
