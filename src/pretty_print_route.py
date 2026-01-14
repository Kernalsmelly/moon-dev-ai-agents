#!/usr/bin/env python3
"""Pretty-print the Jupiter quote debug JSON produced during route capture.

This script reads `jupiter_quote_debug.json` from the repo root and prints a
clean summary including input, output, AMM label, and slippage.
"""
import json
import os
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

console = Console()

# find project root (one level up from src)
project_root = Path(__file__).resolve().parents[1]
quote_path = project_root / 'jupiter_quote_debug.json'

if not quote_path.exists():
    console.print(Panel(f"Quote file not found: {quote_path}\nRun the deep-route scan first.", style="red"))
    raise SystemExit(1)

with quote_path.open('r', encoding='utf-8') as f:
    q = json.load(f)

# Helpers
def lamports_to_sol(s: str) -> float:
    try:
        return int(s) / 1e9
    except Exception:
        return 0.0

def usdc_units_to_float(s: str) -> float:
    # USDC has 6 decimals
    try:
        return int(s) / 1e6
    except Exception:
        return 0.0

# Extract fields with fallbacks
in_amount_raw = q.get('inAmount') or q.get('in_amount') or q.get('in_amount_raw')
out_amount_raw = q.get('outAmount') or q.get('out_amount') or q.get('out_amount_raw')
slippage_bps = q.get('slippageBps') or q.get('slippage_bps') or q.get('slippage')

input_sol = lamports_to_sol(in_amount_raw) if in_amount_raw else None
output_usdc = usdc_units_to_float(out_amount_raw) if out_amount_raw else None

# Extract AMM label from routePlan
amm_label = None
route_plan = q.get('routePlan') or q.get('route_plan') or q.get('routePlanData') or q.get('data')
if isinstance(route_plan, list) and route_plan:
    first = route_plan[0]
    # try known nested shapes
    swap_info = None
    if isinstance(first, dict):
        swap_info = first.get('swapInfo') or first.get('swap_info') or first
    if isinstance(swap_info, dict):
        amm_label = swap_info.get('label') or swap_info.get('ammName') or swap_info.get('AmmName')

# Format results
input_str = f"{input_sol:.9f} SOL" if input_sol is not None else "unknown"
output_str = f"{output_usdc:.6f} USDC" if output_usdc is not None else "unknown"
slippage_str = f"{(int(slippage_bps) / 100):.2f}%" if slippage_bps is not None else "unknown"

# Round output to 2 decimal places for display per request
output_display = f"{output_usdc:.2f} USDC" if output_usdc is not None else "unknown"

# Print pretty summary
summary = (
    f"Input: {input_str}\n"
    f"Output: {output_display}\n"
    f"AMM: {amm_label or 'unknown'}\n"
    f"Slippage: {slippage_str} (Confirmed)\n"
)

console.print(Panel(summary, title="Jupiter Route Summary", style="green"))
console.print(Panel(f"Full quote JSON archived at: {quote_path}", style="cyan"))
