#!/usr/bin/env python3
"""Environment & config audit for data ingestion credentials.

Scans the repository `.env` and `src/config.py` for known keys and prints
a status report. Does NOT print full secret values; shows a short prefix.

Run: .venv/bin/python src/check_env_status.py
"""
import os
import sys
import re
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# make repo root importable when running the script directly
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

try:
    import src.config as config
except Exception:
    config = None

console = Console()
ENV_PATH = os.path.join(project_root, ".env")

# Keys we'll look for across .env and src/config.py
KEYS_TO_CHECK = [
    # RPC / node
    "RPC_ENDPOINT",
    "RPC_URL",
    "SOLANA_RPC",
    # Providers / block explorers / indexers
    "ALCHEMY_API_KEY",
    "HELIUS_API_KEY",
    "RPC_PROVIDER",  # generic
    # LLM / AI
    "ANTHROPIC_API_KEY",
    "OPENAI_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_KEY",
    "GROK_API_KEY",
    # Market data
    "COINGECKO_API_KEY",
    "JUPITER_API_KEY",
    # Exchange
    "HYPERLIQUID_API_KEY",
    "HYPERLIQUID_API_SECRET",
    # Wallets / private keys
    "SOLANA_PRIVATE_KEY",
    # Other
    "TWITTER_API_KEY",
]

# Additional config variables (lower-case) that may be important
CONFIG_VARS = ["address", "symbol"]


def parse_dotenv(path):
    """Return dict of KEY->value from a .env-like file without exporting to os.environ."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # KEY=VALUE (may contain = in the value)
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Remove surrounding quotes
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            result[key] = val
    return result


def short_display(val, length=6):
    if val is None:
        return ""
    s = str(val)
    if not s:
        return ""
    prefix = s[:length]
    return f"Starts with '{prefix}...'"


def domain_from_url(url):
    if not url:
        return None
    if not re.match(r"^https?://", url):
        url = "https://" + url
    try:
        p = urlparse(url)
        return p.netloc
    except Exception:
        return None


def detect_active_sources(found_keys):
    """Map found keys to friendly provider names."""
    sources = set()
    fk = set(found_keys)
    if any(k in fk for k in ("ALCHEMY_API_KEY",)):
        sources.add("Alchemy")
    if any(k in fk for k in ("HELIUS_API_KEY",)):
        sources.add("Helius")
    if any(k in fk for k in ("ANTHROPIC_API_KEY",)):
        sources.add("Anthropic")
    if any(k in fk for k in ("OPENAI_KEY",)):
        sources.add("OpenAI")
    if any(k in fk for k in ("COINGECKO_API_KEY",)):
        sources.add("CoinGecko")
    if any(k in fk for k in ("JUPITER_API_KEY",)):
        sources.add("Jupiter")
    if any(k in fk for k in ("HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET")):
        sources.add("HyperLiquid")
    if any(k in fk for k in ("GROQ_API_KEY", "GROK_API_KEY")):
        sources.add("Groq/xAI")
    if any(k in fk for k in ("DEEPSEEK_KEY",)):
        sources.add("DeepSeek")
    if any(k in fk for k in ("SOLANA_PRIVATE_KEY", "address")):
        sources.add("Wallet / Solana Key")
    if any(k in fk for k in ("RPC_ENDPOINT", "RPC_URL", "SOLANA_RPC")):
        sources.add("Solana RPC Endpoint")
    return sorted(sources)


def main():
    env = parse_dotenv(ENV_PATH)

    # Build table
    table = Table(title="Environment & Config Status", show_edge=False)
    table.add_column("Key", style="cyan")
    table.add_column("Location", style="green")
    table.add_column("Status", style="magenta")

    found_keys = []

    # Check keys list
    for key in KEYS_TO_CHECK:
        if key in env and env.get(key):
            table.add_row(key, ".env", short_display(env.get(key)))
            found_keys.append(key)
        elif config is not None and hasattr(config, key):
            val = getattr(config, key)
            table.add_row(key, "src/config.py", short_display(val))
            found_keys.append(key)
        else:
            table.add_row(key, "-", "MISSING")

    # Check lower-case config vars
    for v in CONFIG_VARS:
        if config is not None and hasattr(config, v):
            val = getattr(config, v)
            table.add_row(v, "src/config.py", short_display(val))
            found_keys.append(v)
        else:
            table.add_row(v, "-", "MISSING")

    # Determine RPC URL used by the project (precedence: env RPC_ENDPOINT -> config RPC_URL -> config SOLANA_RPC -> default)
    rpc_candidate = None
    if env.get("RPC_ENDPOINT"):
        rpc_candidate = env.get("RPC_ENDPOINT")
        rpc_location = ".env (RPC_ENDPOINT)"
    elif config is not None and hasattr(config, "RPC_URL") and getattr(config, "RPC_URL"):
        rpc_candidate = getattr(config, "RPC_URL")
        rpc_location = "src/config.py (RPC_URL)"
    elif config is not None and hasattr(config, "SOLANA_RPC") and getattr(config, "SOLANA_RPC"):
        rpc_candidate = getattr(config, "SOLANA_RPC")
        rpc_location = "src/config.py (SOLANA_RPC)"
    else:
        rpc_candidate = "https://api.mainnet-beta.solana.com"
        rpc_location = "default"

    rpc_domain = domain_from_url(rpc_candidate)

    # Active data sources
    active = detect_active_sources(found_keys)

    # Print
    console.print(Panel(table, title="Config Audit", expand=False))

    console.print("RPC in use: ", rpc_location, rpc_domain if rpc_domain else rpc_candidate)

    if active:
        console.print("Active data sources:")
        for s in active:
            console.print(f" - {s}")
    else:
        console.print("No active data source credentials detected.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        console.print(Panel(f"Fatal error during env audit: {e}", style="red"))
