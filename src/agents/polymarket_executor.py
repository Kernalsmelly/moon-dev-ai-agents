"""
Polymarket CLOB Executor

Provides a thin wrapper around the py-clob-client to place/cancel orders and
query open orders / positions. Designed for safe use in this repo: default
mode is dry-run (paper). No keys are hard-coded; they are read from .env.

Usage: set PRIVATE_KEY and POLYGON_CHAIN_ID in your .env, then create an
executor and call place_limit_order / cancel_order / get_open_orders.

This file follows the project's console logging style (termcolor.cprint)
and contains defensive handling if the clob client library isn't available.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from pathlib import Path
from dotenv import load_dotenv
from termcolor import cprint

# Load environment from repository root or caller's CWD
project_root = str(Path(__file__).parent.parent.parent)
dotenv_path = os.path.join(project_root, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    # Fall back to default env loading (useful in container/venv setups)
    load_dotenv()

# Try to import official py-clob-client in a forgiving way
ClobClient = None
try:
    # Common layout: clob.client.ClobClient
    from clob.client import ClobClient  # type: ignore
except Exception:
    try:
        # Alternative import path: clob.ClobClient
        from clob import ClobClient  # type: ignore
    except Exception:
        ClobClient = None


class PolymarketExecutor:
    """Wrapper for CLOB order execution.

    Attributes:
        private_key: str (read from env)
        chain_id: int (read from env or passed)
        host: str (CLOB host)
        dry_run: bool (if True, do not submit orders)
        client: optional ClobClient instance
    """

    def __init__(self,
                 private_key: Optional[str] = None,
                 chain_id: Optional[int] = None,
                 host: str = "https://clob.polymarket.com",
                 dry_run: bool = True):
        self.private_key = private_key or os.getenv("PRIVATE_KEY")
        env_chain = os.getenv("POLYGON_CHAIN_ID")
        self.chain_id = int(chain_id or env_chain or 137)
        self.host = host
        self.dry_run = dry_run

        self.client = None

        if not self.private_key:
            cprint("⚠️ PRIVATE_KEY not set in environment - executor will run in read-only/dry mode", "yellow")

        if ClobClient is None:
            cprint("⚠️ py-clob-client library not available. Live order functions will be disabled.", "red")
        else:
            try:
                if not self.dry_run and self.private_key:
                    # Initialize the client only when not in dry-run to avoid accidental live orders
                    self.client = ClobClient(host=self.host, key=self.private_key, chain_id=self.chain_id)
                    cprint(f"✅ ClobClient initialized (host={self.host}, chain_id={self.chain_id})", "green")
                else:
                    cprint("ℹ️ Executor initialized in dry-run mode (no live orders will be submitted)", "cyan")
            except Exception as e:
                cprint(f"❌ Error initializing ClobClient: {e}", "red")
                self.client = None

    # ------------------------- Helper utilities -------------------------
    def _log(self, message: str, level: str = "info") -> None:
        if level == "info":
            cprint(message, "cyan")
        elif level == "success":
            cprint(message, "green")
        elif level == "warn":
            cprint(message, "yellow")
        elif level == "error":
            cprint(message, "red")
        else:
            cprint(message, "white")

    def _ensure_client(self) -> bool:
        if self.dry_run:
            self._log("Dry-run enabled: skipping live CLOB client calls", "warn")
            return False
        if ClobClient is None:
            self._log("py-clob-client not installed; cannot perform live order calls", "error")
            return False
        if self.client is None:
            try:
                # Attempt to create client if possible (private_key must be present)
                if not self.private_key:
                    self._log("PRIVATE_KEY missing; cannot initialize ClobClient", "error")
                    return False
                self.client = ClobClient(host=self.host, key=self.private_key, chain_id=self.chain_id)
                self._log("✅ ClobClient initialized on-demand", "success")
            except Exception as e:
                self._log(f"❌ Failed to initialize ClobClient: {e}", "error")
                return False
        return True

    # ------------------------- Public API -------------------------
    def place_limit_order(self, market_token_id: str, side: str, price: float, size: float) -> Dict[str, Any]:
        """Place a limit order on the Polymarket CLOB.

        Args:
            market_token_id: market identifier/token (string as used by CLOB)
            side: 'buy' or 'sell'
            price: price per unit (float)
            size: size in base units or USD depending on market (float)

        Returns:
            dict: response with keys 'success' (bool) and 'result' or 'error'
        """
        side_norm = side.lower()
        if side_norm in ("bid", "buy"):
            side_norm = "buy"
        elif side_norm in ("ask", "sell"):
            side_norm = "sell"
        else:
            return {"success": False, "error": f"Invalid side: {side}. Use 'buy' or 'sell'"}

        self._log(f"Placing limit order [dry_run={self.dry_run}] -> {side_norm.upper()} {size} @ {price} on {market_token_id}")

        if not self._ensure_client():
            # Dry-run or missing client: return simulated response
            simulated = {
                "success": True,
                "result": {
                    "simulated": True,
                    "market_token_id": market_token_id,
                    "side": side_norm,
                    "price": price,
                    "size": size,
                    "timestamp": int(time.time())
                }
            }
            self._log(f"Simulated order: {simulated['result']}", "info")
            return simulated

        # Live mode: attempt to place an order using the clob client
        try:
            # Try a few common method names to be compatible with different client versions
            if hasattr(self.client, "place_order"):
                resp = self.client.place_order(token=market_token_id, side=side_norm, price=price, size=size)
            elif hasattr(self.client, "place_limit_order"):
                resp = self.client.place_limit_order(market_token_id, side_norm, price, size)
            elif hasattr(self.client, "create_order"):
                resp = self.client.create_order(market_token_id, side_norm, price, size)
            else:
                return {"success": False, "error": "ClobClient does not expose a known order placement method"}

            return {"success": True, "result": resp}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order by ID.

        Returns True if cancel acknowledged, False otherwise.
        """
        self._log(f"Cancelling order [dry_run={self.dry_run}]: {order_id}")
        if not self._ensure_client():
            self._log("Dry-run or client unavailable: pretend cancel succeeded", "warn")
            return True

        try:
            if hasattr(self.client, "cancel_order"):
                resp = self.client.cancel_order(order_id)
                # Some clients return a truthy value or dict
                return bool(resp)
            elif hasattr(self.client, "cancel"):
                resp = self.client.cancel(order_id)
                return bool(resp)
            else:
                self._log("ClobClient does not provide cancel API", "error")
                return False

        except Exception as e:
            self._log(f"❌ Cancel failed: {e}", "error")
            return False

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Return current resting/open orders for the configured key/account.

        If client is unavailable, returns an empty list.
        """
        self._log("Fetching open orders...")
        if not self._ensure_client():
            self._log("Client unavailable: returning empty open orders list", "warn")
            return []

        try:
            if hasattr(self.client, "get_open_orders"):
                resp = self.client.get_open_orders()
            elif hasattr(self.client, "open_orders"):
                resp = self.client.open_orders()
            elif hasattr(self.client, "list_orders"):
                resp = self.client.list_orders(status="open")
            else:
                self._log("ClobClient does not expose a known open orders method", "error")
                return []

            # Normalize to list
            if isinstance(resp, dict) and resp.get("orders"):
                return resp.get("orders")
            if isinstance(resp, list):
                return resp
            return [resp]

        except Exception as e:
            self._log(f"❌ Error fetching open orders: {e}", "error")
            return []

    def get_positions(self) -> Dict[str, Any]:
        """Return positions for the account if API supports it.

        Returns an empty dict when not supported or in dry-run.
        """
        self._log("Fetching positions...")
        if not self._ensure_client():
            self._log("Client unavailable: returning empty positions", "warn")
            return {}

        try:
            if hasattr(self.client, "get_positions"):
                resp = self.client.get_positions()
            elif hasattr(self.client, "positions"):
                resp = self.client.positions()
            else:
                self._log("ClobClient does not expose a positions API", "warn")
                return {}

            return resp or {}

        except Exception as e:
            self._log(f"❌ Error fetching positions: {e}", "error")
            return {}


# ------------------------- Example usage (commented) -------------------------
# Example (dry-run by default):
# from src.agents.polymarket_executor import PolymarketExecutor
# exec = PolymarketExecutor(dry_run=True)
# resp = exec.place_limit_order(market_token_id="0xabc...", side="buy", price=0.5, size=10)
# print(resp)


if __name__ == "__main__":
    # Quick smoke test in dry-run mode
    ex = PolymarketExecutor(dry_run=True)
    demo = ex.place_limit_order("demo_market", "buy", 0.5, 1)
    cprint(f"Demo response: {demo}", "cyan")
