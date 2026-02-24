"""
🌙 Moon Dev's Nice Functions - A collection of utility functions for trading
Built with love by Moon Dev 🚀
"""

import src.config as config
import requests
import pandas as pd
import pprint
import re as reggie
import os
import time
import json
import random
import pandas_ta as ta
from datetime import datetime, timedelta
import math
from termcolor import cprint
from dotenv import load_dotenv
import shutil
import atexit

# Load environment variables
load_dotenv()

# Get API keys from environment
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
if not BIRDEYE_API_KEY:
    raise ValueError("🚨 BIRDEYE_API_KEY not found in environment variables!")

sample_address = "2yXTyarttn2pTZ6cwt4DqmrRuBw1G7pmFv9oT6MStdKP"

BASE_URL = "https://public-api.birdeye.so/defi"

# Create temp directory and register cleanup
os.makedirs('temp_data', exist_ok=True)

# Simple in-memory cache for token symbols to avoid 'Unknown' labels
TOKEN_CACHE = {}

# Birdeye throttles on free tiers with responses like:
# {"success":false,"message":"Compute units usage limit exceeded"}
# We treat that as a soft rate limit and apply a global cooldown so callers don't spin.
BIRDEYE_COOLDOWN_UNTIL = 0.0
BIRDEYE_BACKOFF_S = 0.0
BIRDEYE_FAILS = 0
TOKEN_SECURITY_CACHE: dict[str, tuple[float, dict]] = {}


def _birdeye_get_data(url: str, headers: dict, *, timeout_s: float = 6.0) -> dict | None:
    global BIRDEYE_COOLDOWN_UNTIL, BIRDEYE_BACKOFF_S
    global BIRDEYE_FAILS
    now = time.time()
    if BIRDEYE_COOLDOWN_UNTIL and now < BIRDEYE_COOLDOWN_UNTIL:
        return None

    def _apply_backoff(base_s: float) -> None:
        """Global cooldown to avoid spinning when Birdeye throttles."""
        global BIRDEYE_COOLDOWN_UNTIL, BIRDEYE_BACKOFF_S, BIRDEYE_FAILS
        BIRDEYE_FAILS = int(BIRDEYE_FAILS or 0) + 1
        base_s = float(base_s)
        BIRDEYE_BACKOFF_S = min(600.0, max(base_s, (BIRDEYE_BACKOFF_S * 2.0) if BIRDEYE_BACKOFF_S else base_s))
        jitter = random.uniform(0.0, min(3.0, BIRDEYE_BACKOFF_S * 0.1))
        BIRDEYE_COOLDOWN_UNTIL = now + BIRDEYE_BACKOFF_S + jitter

    try:
        resp = requests.get(url, headers=headers, timeout=timeout_s)
        try:
            j = resp.json()
        except Exception:
            j = None

        # hard rate-limit
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            try:
                ra_s = float(ra) if ra is not None else None
            except Exception:
                ra_s = None
            _apply_backoff(ra_s if ra_s is not None and ra_s > 0 else 10.0)
            return None

        # transient provider errors, treat as soft-throttle
        if 500 <= int(resp.status_code) <= 599:
            _apply_backoff(5.0)
            return None

        # soft rate-limit via "compute units" messages (often still HTTP 200)
        if isinstance(j, dict) and j.get("success") is False:
            msg = str(j.get("message") or "")
            if "compute units" in msg.lower() or "usage limit" in msg.lower():
                _apply_backoff(20.0)
                return None
        if resp.status_code != 200 or not isinstance(j, dict):
            return None
        if j.get("success") is False:
            return None
        # success resets global cooldown
        BIRDEYE_FAILS = 0
        BIRDEYE_BACKOFF_S = 0.0
        BIRDEYE_COOLDOWN_UNTIL = 0.0
        return j.get("data") or {}
    except Exception:
        _apply_backoff(5.0)
        return None

def get_cached_symbol(address: str) -> str:
    """
    Return cached symbol for address, or a short fallback (first 4 chars)
    to avoid 'Unknown' labels in logs/Discord.
    """
    try:
        sym = TOKEN_CACHE.get(address)
        if sym:
            return sym
    except Exception:
        pass

    # Fallback to first 4 chars to keep messages readable
    return str(address)[:4]


def get_priority_fee(mode: str = 'standard') -> int:
    """
    Return a prioritization fee (in lamports) based on the urgency mode.
    'standard' is a low value, 'panic' is high.

    Values: standard=50_000, panic=250_000
    """
    try:
        if mode == 'panic':
            return 250_000
        return 50_000
    except Exception:
        return 50_000


# Persistence helpers for position state (highest_price_seen, de_risked, entry_price)
# Now backed by SQLite for crash-proof persistence and trade history
POS_STATE_FILE = os.path.join('data', 'positions_state.json')  # Legacy path (for reference)

# Use SQLite-backed position store
try:
    from src.position_store import (
        load_positions_state,
        save_positions_state,
        get_position,
        update_position,
        record_trade,
        get_performance_summary,
    )
    _USE_SQLITE_STORE = True
except ImportError:
    # Fallback to JSON if position_store not available
    _USE_SQLITE_STORE = False

    def load_positions_state():
        try:
            os.makedirs('data', exist_ok=True)
            if os.path.exists(POS_STATE_FILE):
                with open(POS_STATE_FILE, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_positions_state(state: dict):
        try:
            os.makedirs('data', exist_ok=True)
            with open(POS_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
            return True
        except Exception as e:
            print('Failed to save positions state:', e)
            return False

    def get_position(mint: str):
        state = load_positions_state()
        return state.get(mint)

    def update_position(mint: str, **kwargs):
        state = load_positions_state()
        if mint not in state:
            state[mint] = {}
        state[mint].update(kwargs)
        return save_positions_state(state)

    def record_trade(**kwargs):
        pass  # No-op in fallback mode

    def get_performance_summary(**kwargs):
        return {}


def verify_transaction_structure(tx_bytes: bytes):
    """
    Best-effort verification that a VersionedTransaction (bytes) contains
    ComputeBudget set_compute_unit_limit followed by set_compute_unit_price
    instructions in that order. Prints a confirmation message when found.
    """
    try:
        # Try solders VersionedTransaction first
        try:
            from solders.transaction import VersionedTransaction
            tx = VersionedTransaction.from_bytes(tx_bytes)
            instrs = getattr(tx.message, 'instructions', []) or []
            # stringify instructions for heuristic matching
            instr_texts = [str(i).lower() for i in instrs]
        except Exception:
            # Fallback to solana-py decoding if available
            try:
                from solana.transaction import VersionedTransaction
                tx = VersionedTransaction.deserialize(tx_bytes)
                instrs = getattr(tx.message, 'instructions', []) or []
                instr_texts = [str(i).lower() for i in instrs]
            except Exception:
                print('⚠️ Could not decode VersionedTransaction for structure verification (no supported library).')
                return False

        # Heuristic: look for keywords 'compute' + 'limit' and 'compute' + 'price'
        idx_limit = next((i for i, t in enumerate(instr_texts) if 'compute' in t and 'limit' in t), None)
        idx_price = next((i for i, t in enumerate(instr_texts) if 'compute' in t and 'price' in t), None)

        if idx_limit is not None and idx_price is not None and idx_limit < idx_price:
            print('✅ Transaction structure OK: SetComputeUnitLimit followed by SetComputeUnitPrice found')
            return True
        else:
            print('⚠️ Transaction structure does not contain compute-budget price/limit in expected order')
            return False

    except Exception as e:
        print('⚠️ Error while verifying transaction structure:', e)
        return False

def cleanup_temp_data():
    if os.path.exists('temp_data'):
        print("🧹 Moon Dev cleaning up temporary data...")
        shutil.rmtree('temp_data')

atexit.register(cleanup_temp_data)

# Custom function to print JSON in a human-readable format
def print_pretty_json(data):
    pp = pprint.PrettyPrinter(indent=4)
    pp.pprint(data)

# Function to print JSON in a human-readable format - assuming you already have it as print_pretty_json
# Helper function to find URLs in text
def find_urls(string):
    # Regex to extract URLs
    return reggie.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', string)

# UPDATED TO RMEOVE THE OTHER ONE so now we can just use this filter instead of filtering twice
def token_overview(address):
    """
    Fetch token overview for a given address and return structured information, including specific links,
    and assess if any price change suggests a rug pull.
    """

    print(f'Getting the token overview for {address}')
    overview_url = f"{BASE_URL}/token_overview?address={address}"
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY}

    # Use the shared Birdeye helper so we respect global cooldowns/backoff.
    overview_data = _birdeye_get_data(overview_url, headers, timeout_s=6.0) or {}
    result = {}

    if overview_data:

        # Retrieve buy1h, sell1h, and calculate trade1h
        buy1h = overview_data.get('buy1h', 0)
        sell1h = overview_data.get('sell1h', 0)
        trade1h = buy1h + sell1h

        # Add the calculated values to the result
        result['buy1h'] = buy1h
        result['sell1h'] = sell1h
        result['trade1h'] = trade1h

        # Calculate buy and sell percentages
        total_trades = trade1h  # Assuming total_trades is the sum of buy and sell
        buy_percentage = (buy1h / total_trades * 100) if total_trades else 0
        sell_percentage = (sell1h / total_trades * 100) if total_trades else 0
        result['buy_percentage'] = buy_percentage
        result['sell_percentage'] = sell_percentage

        # Check if trade1h is bigger than MIN_TRADES_LAST_HOUR
        result['minimum_trades_met'] = True if trade1h >= config.MIN_TRADES_LAST_HOUR else False

        # Extract price changes over different timeframes
        price_changes = {k: v for k, v in overview_data.items() if 'priceChange' in k}
        result['priceChangesXhrs'] = price_changes

        # Check for rug pull indicator
        rug_pull = any(value < -80 for key, value in price_changes.items() if value is not None)
        result['rug_pull'] = rug_pull
        if rug_pull:
            print("Warning: Price change percentage below -80%, potential rug pull")

        # Extract other metrics
        unique_wallet2hr = overview_data.get('uniqueWallet24h', 0)
        v24USD = overview_data.get('v24hUSD', 0)
        watch = overview_data.get('watch', 0)
        view24h = overview_data.get('view24h', 0)
        liquidity = overview_data.get('liquidity', 0)
        mc = overview_data.get('mc', 0)  # Get market cap

        # Add the retrieved data to result
        result.update({
            'uniqueWallet2hr': unique_wallet2hr,
            'v24USD': v24USD,
            'watch': watch,
            'view24h': view24h,
            'liquidity': liquidity,
            'mc': mc  # Add market cap to result
        })

        # Extract and process description links if extensions are not None
        extensions = overview_data.get('extensions', {})
        description = extensions.get('description', '') if extensions else ''
        urls = find_urls(description)
        links = []
        for url in urls:
            if 't.me' in url:
                links.append({'telegram': url})
            elif 'twitter.com' in url:
                links.append({'twitter': url})
            elif 'youtube' not in url:  # Assume other URLs are for website
                links.append({'website': url})

        # Add extracted links to result
        result['description'] = links


        # If we can derive a token symbol/name, cache it for later
        token_name = overview_data.get('tokenName') or overview_data.get('name') or overview_data.get('symbol')
        if token_name:
            try:
                TOKEN_CACHE[address] = token_name
            except Exception:
                pass

        # Return result dictionary with all the data
        return result
    else:
        print(f"Failed to retrieve token overview for address {address} (Birdeye unavailable or rate-limited)")
        return None


def token_security_info(address):

    '''

    bigmatter
​freeze authority is like renouncing ownership on eth

    Token Security Info:
{   'creationSlot': 242801308,
    'creationTime': 1705679481,
    'creationTx': 'ZJGoayaNDf2dLzknCjjaE9QjqxocA94pcegiF1oLsGZ841EMWBEc7TnDKLvCnE8cCVfkvoTNYCdMyhrWFFwPX6R',
    'creatorAddress': 'AGWdoU4j4MGJTkSor7ZSkNiF8oPe15754hsuLmwcEyzC',
    'creatorBalance': 0,
    'creatorPercentage': 0,
    'freezeAuthority': None,
    'freezeable': None,
    'isToken2022': False,
    'isTrueToken': None,
    'lockInfo': None,
    'metaplexUpdateAuthority': 'AGWdoU4j4MGJTkSor7ZSkNiF8oPe15754hsuLmwcEyzC',
    'metaplexUpdateAuthorityBalance': 0,
    'metaplexUpdateAuthorityPercent': 0,
    'mintSlot': 242801308,
    'mintTime': 1705679481,
    'mintTx': 'ZJGoayaNDf2dLzknCjjaE9QjqxocA94pcegiF1oLsGZ841EMWBEc7TnDKLvCnE8cCVfkvoTNYCdMyhrWFFwPX6R',
    'mutableMetadata': True,
    'nonTransferable': None,
    'ownerAddress': None,
    'ownerBalance': None,
    'ownerPercentage': None,
    'preMarketHolder': [],
    'top10HolderBalance': 357579981.3372284,
    'top10HolderPercent': 0.6439307358062863,
    'top10UserBalance': 138709981.9366756,
    'top10UserPercent': 0.24978920911102176,
    'totalSupply': 555308143.3354646,
    'transferFeeData': None,
    'transferFeeEnable': None}
    '''

    # API endpoint for getting token security information
    url = f"{BASE_URL}/token_security?address={address}"
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY}

    # Use the shared Birdeye helper so we respect global cooldowns/backoff.
    security_data = _birdeye_get_data(url, headers, timeout_s=6.0)

    if security_data:
        # Cache any symbol/name we can find to reduce Unknown labels elsewhere
        token_name = security_data.get('tokenName') or security_data.get('name') or security_data.get('symbol')
        if token_name:
            try:
                TOKEN_CACHE[address] = token_name
            except Exception:
                pass

        print_pretty_json(security_data)
    else:
        print("Failed to retrieve token security info (Birdeye unavailable or rate-limited)")


def token_security_raw(address: str):
    """Return Birdeye token_security data without printing/logging.

    Returns dict or None if unavailable.
    """
    api_key = getattr(config, "BIRDEYE_API_KEY", None) or os.getenv("BIRDEYE_API_KEY")
    if not api_key:
        return None
    url = f"{BASE_URL}/token_security?address={address}"
    headers = {"X-API-KEY": api_key}
    try:
        cached = TOKEN_SECURITY_CACHE.get(address)
        if cached and (time.time() - cached[0]) < 900:
            return cached[1] or None
        data = _birdeye_get_data(url, headers, timeout_s=6.0)
        if not data:
            return None
        TOKEN_SECURITY_CACHE[address] = (time.time(), data)
        return data or None
    except Exception:
        return None


def send_discord_message(content: str):
    """
    Send a simple text message to a Discord webhook if configured via
    the DISCORD_WEBHOOK environment variable. Falls back to printing
    when the webhook is not configured.
    """
    webhook = os.getenv("DISCORD_WEBHOOK") or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        # Webhook not configured — print so we still have an audit trail
        print(f"[Discord disabled] {content}")
        return False

    try:
        res = requests.post(webhook, json={"content": content}, timeout=5)
        if res.status_code in (200, 204):
            return True
        else:
            print(f"Failed to send discord message, status={res.status_code}: {res.text}")
            return False
    except Exception as e:
        print(f"Exception sending discord message: {e}")
        return False


def is_momentum_reject(address: str):
    """
    Query Birdeye's token_security endpoint and apply a conservative
    "momentum reject" rule: if a mint authority exists (not null) OR
    freeze authority / freezeable is enabled, mark as a reject.

    Returns: (is_reject: bool, token_name_or_address: str, details: dict)
    """
    url = f"{BASE_URL}/token_security?address={address}"
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY}

    try:
        cached = TOKEN_SECURITY_CACHE.get(address)
        if cached and (time.time() - cached[0]) < 900:
            data = cached[1] or {}
        else:
            data = _birdeye_get_data(url, headers, timeout_s=6.0) or {}
            if data:
                TOKEN_SECURITY_CACHE[address] = (time.time(), data)
        if not data:
            # Treat rate limits / transient errors as "unknown", not an automatic reject.
            return (False, address, {"error": "birdeye_unavailable"})

        # Try to derive a friendly token name from available fields
        token_name = data.get('tokenName') or data.get('name') or data.get('symbol') or address

        # Cache token name/symbol if available
        if token_name and token_name != address:
            try:
                TOKEN_CACHE[address] = token_name
            except Exception:
                pass

        # Fields observed in Birdeye responses: freezeAuthority, freezeable, metaplexUpdateAuthority, creatorAddress, ownerAddress
        mint_fields = [
            data.get('mintAuthority'),
            data.get('mint_authority'),
            data.get('metaplexUpdateAuthority'),
            data.get('creatorAddress'),
            data.get('ownerAddress'),
            data.get('owner'),
        ]

        freeze_field = data.get('freezeAuthority') if 'freezeAuthority' in data else data.get('freeze_authority', None)
        freezeable = data.get('freezeable') or data.get('isFreezeable') or False

        mint_present = any(x for x in mint_fields if x not in (None, '', []))
        freeze_present = (freeze_field not in (None, '', False)) or bool(freezeable)

        reasons = {}
        if mint_present:
            reasons['mint'] = [x for x in mint_fields if x not in (None, '', [])]
        if freeze_present:
            reasons['freeze'] = {'freezeAuthority': freeze_field, 'freezeable': freezeable}

        is_reject = mint_present or freeze_present

        details = {
            'raw': data,
            'mint_present': mint_present,
            'freeze_present': freeze_present,
            'reasons': reasons
        }

        return (is_reject, token_name, details)

    except Exception as e:
        return (False, address, {'error': str(e)})

def token_creation_info(address):

    '''
    output sampel =

    Token Creation Info:
{   'decimals': 9,
    'owner': 'AGWdoU4j4MGJTkSor7ZSkNiF8oPe15754hsuLmwcEyzC',
    'slot': 242801308,
    'tokenAddress': '9dQi5nMznCAcgDPUMDPkRqG8bshMFnzCmcyzD8afjGJm',
    'txHash': 'ZJGoayaNDf2dLzknCjjaE9QjqxocA94pcegiF1oLsGZ841EMWBEc7TnDKLvCnE8cCVfkvoTNYCdMyhrWFFwPX6R'}
    '''
    # API endpoint for getting token creation information
    url = f"{BASE_URL}/token_creation_info?address={address}"
    headers = {"X-API-KEY": BIRDEYE_API_KEY}

    # Sending a GET request to the API
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        # Parse the JSON response
        creation_data = response.json()['data']
        print_pretty_json(creation_data)
    else:
        print("Failed to retrieve token creation info:", response.status_code)

def market_buy(token, amount, slippage=None, mode: str = 'standard'):
    """
    Market buy via Jupiter Lite. mode controls priority fee escalation.
    slippage is optional (BPS). If not provided, a sensible default is used.
    """
    import requests
    import json
    import base64
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts

    KEY = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))
    if not KEY:
        raise ValueError("🚨 SOLANA_PRIVATE_KEY not found in environment variables!")

    # Default slippage (BPS) if caller didn't provide one
    SLIPPAGE = slippage if slippage is not None else 500

    QUOTE_TOKEN = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # usdc

    http_client = Client(os.getenv("RPC_ENDPOINT"))
    if not http_client:
        raise ValueError("🚨 RPC_ENDPOINT not found in environment variables!")

    # Convert amount from dollars to USDC units (6 decimals)
    amount_in_units = int(int(amount) if isinstance(amount, str) and amount.isdigit() else float(amount) * 1_000_000)
    print(f"💰 Converting ${amount} to {amount_in_units:,} USDC units")

    # Use Jupiter Lite API (faster and more efficient)
    quote = requests.get(f'https://lite-api.jup.ag/swap/v1/quote?inputMint={QUOTE_TOKEN}&outputMint={token}&amount={amount_in_units}&slippageBps={SLIPPAGE}').json()

    priority_fee_value = get_priority_fee(mode)

    txRes = requests.post('https://lite-api.jup.ag/swap/v1/swap',
                          headers={"Content-Type": "application/json"},
                          data=json.dumps({
                              "quoteResponse": quote,
                              "userPublicKey": str(KEY.pubkey()),
                              "prioritizationFeeLamports": priority_fee_value
                          })).json()

    swapTx = base64.b64decode(txRes['swapTransaction'])
    tx1 = VersionedTransaction.from_bytes(swapTx)

    # Try to prepend compute-budget instructions for extra priority (best-effort)
    try:
        # Try solders compute budget
        try:
            from solders.programs.compute_budget import ComputeBudgetProgram
            limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
            price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
            if hasattr(tx1.message, 'instructions'):
                tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
        except Exception:
            # Try solana-py compute budget
            try:
                from solana.compute_budget import ComputeBudgetProgram
                limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
                price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
                if hasattr(tx1.message, 'instructions'):
                    tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
            except Exception:
                # If both libs fail, just continue; prioritizationFeeLamports still applied
                print('⚠️ Could not prepend compute budget instructions (library mismatch). Falling back to prioritizationFee only.')
    except Exception as e:
        print('⚠️ Unexpected error while attempting compute budget prepend:', e)

    # Record pre-trade on-chain balance as a fallback if tx metadata is not available
    usd_spent = float(amount)
    pre_balance = 0.0
    try:
        pre_balance = float(get_position(token))
    except Exception:
        pre_balance = 0.0

    tx = VersionedTransaction(tx1.message, [KEY])
    txId = http_client.send_raw_transaction(bytes(tx), TxOpts(skip_preflight=True)).value
    print(f"https://solscan.io/tx/{str(txId)}")

    # Confirm transaction: poll signature status and get_transaction until confirmed/finalized or timeout
    tx_meta = None
    confirmed = False
    for _ in range(30):
        try:
            time.sleep(1)
            # Check signature status first (faster) to detect 'confirmed' or 'finalized'
            try:
                sig_status = http_client.get_signature_statuses([txId])
                val = sig_status.get('result', {}).get('value', [None])[0]
                if val and val.get('confirmationStatus') in ('confirmed', 'finalized'):
                    confirmed = True
                    # Try to fetch transaction metadata; if not present yet, we'll still proceed with fallback
                    try:
                        resp = http_client.get_transaction(txId)
                        if resp and resp.get('result'):
                            tx_meta = resp.get('result', {}).get('meta', {}) or {}
                    except Exception:
                        tx_meta = None
                    break
            except Exception:
                # If get_signature_statuses isn't available or fails, fall back to get_transaction
                pass

            # As a secondary check, attempt to fetch full transaction (may contain pre/post balances)
            try:
                resp = http_client.get_transaction(txId)
                if resp and resp.get('result'):
                    tx_meta = resp.get('result', {}).get('meta', {}) or {}
                    confirmed = True
                    break
            except Exception:
                pass
        except Exception:
            pass

    if not confirmed:
        print(f"⚠️ Transaction {txId} not confirmed within timeout; will fallback to balance polling to infer tokens received.")

    # Verify transaction structure (debugging / dry-run helper)
    try:
        verify_transaction_structure(swapTx)
    except Exception:
        pass

    tokens_received = 0.0

    # First try to extract token change from transaction metadata (pre/post token balances)
    try:
        if tx_meta:
            pre_toks = tx_meta.get('preTokenBalances', []) or []
            post_toks = tx_meta.get('postTokenBalances', []) or []

            # helper to find uiAmount for our token mint
            def find_uiamount(lst, mint):
                for entry in lst:
                    try:
                        if entry.get('mint') == mint:
                            ui = entry.get('uiTokenAmount', {}).get('uiAmount')
                            if ui is None:
                                # older fields may use 'uiAmountString' or raw amount
                                ui = float(entry.get('uiTokenAmount', {}).get('amount', 0))
                            return float(ui)
                    except Exception:
                        continue
                return None

            pre_ui = find_uiamount(pre_toks, token)
            post_ui = find_uiamount(post_toks, token)
            if pre_ui is not None and post_ui is not None:
                tokens_received = max(0.0, float(post_ui) - float(pre_ui))
    except Exception:
        tokens_received = 0.0

    # Fallback: if metadata not present or didn't show the token change, poll balances
    if tokens_received == 0.0:
        post_balance = pre_balance
        for _ in range(10):
            try:
                time.sleep(2)
                post_balance = float(get_position(token))
                if post_balance > pre_balance:
                    break
            except Exception:
                pass
        tokens_received = max(0.0, post_balance - pre_balance)

    # Update persisted positions state with precise entry price and amount (immediately)
    try:
        positions = load_positions_state()
        pos = positions.get(token, {})
        prev_amt = float(pos.get('amount', 0) or 0)
        prev_entry = float(pos.get('entry_price', 0) or 0)

        if tokens_received > 0:
            new_amt = prev_amt + tokens_received
            if prev_amt > 0 and prev_entry > 0:
                new_entry = (prev_entry * prev_amt + usd_spent) / new_amt
            else:
                new_entry = usd_spent / tokens_received

            pos['amount'] = new_amt
            pos['entry_price'] = new_entry
            # initialize highest_price_seen to the true entry price if not set or lower
            pos['highest_price_seen'] = float(pos.get('highest_price_seen', new_entry))
            if pos['highest_price_seen'] < new_entry:
                pos['highest_price_seen'] = new_entry
            pos.setdefault('de_risked', False)
            positions[token] = pos
            save_positions_state(positions)
            # Validation log
            try:
                print(f"✅ TRADE VERIFIED | Tokens: {tokens_received} | True Entry: ${round(new_entry,6)} | Priority: {mode}")
            except Exception:
                print(f"✅ TRADE VERIFIED | Tokens: {tokens_received} | True Entry: ${new_entry} | Priority: {mode}")
        else:
            # No tokens detected; still persist pre-existing state if absent
            pos.setdefault('amount', prev_amt)
            pos.setdefault('entry_price', prev_entry)
            pos.setdefault('highest_price_seen', prev_entry or 0)
            pos.setdefault('de_risked', False)
            positions[token] = pos
            save_positions_state(positions)
    except Exception as e:
        print('⚠️ Failed to update positions state after buy:', e)

    return str(txId)



def market_sell(QUOTE_TOKEN, amount, slippage=None, mode: str = 'standard'):
    """
    Market sell via Jupiter Lite. QUOTE_TOKEN is the input mint (token to sell).
    mode: 'standard' or 'panic' — panic increases priority fee and attempts compute-budget.
    slippage is optional BPS.
    """
    import requests
    import json
    import base64
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts

    KEY = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))
    if not KEY:
        raise ValueError("🚨 SOLANA_PRIVATE_KEY not found in environment variables!")

    SLIPPAGE = slippage if slippage is not None else 500

    # token would be usdc for sell orders because we are selling into USDC
    token = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC

    http_client = Client(os.getenv("RPC_ENDPOINT"))
    if not http_client:
        raise ValueError("🚨 RPC_ENDPOINT not found in environment variables!")

    # Use Jupiter Lite API (faster and more efficient)
    quote = requests.get(f'https://lite-api.jup.ag/swap/v1/quote?inputMint={QUOTE_TOKEN}&outputMint={token}&amount={amount}&slippageBps={SLIPPAGE}').json()

    priority_fee_value = get_priority_fee(mode)

    txRes = requests.post('https://lite-api.jup.ag/swap/v1/swap',
                          headers={"Content-Type": "application/json"},
                          data=json.dumps({
                              "quoteResponse": quote,
                              "userPublicKey": str(KEY.pubkey()),
                              "prioritizationFeeLamports": priority_fee_value
                          })).json()

    swapTx = base64.b64decode(txRes['swapTransaction'])
    tx1 = VersionedTransaction.from_bytes(swapTx)

    # Try to prepend compute-budget instructions for extra priority (best-effort)
    try:
        try:
            from solders.programs.compute_budget import ComputeBudgetProgram
            limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
            price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
            if hasattr(tx1.message, 'instructions'):
                tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
        except Exception:
            try:
                from solana.compute_budget import ComputeBudgetProgram
                limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
                price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
                if hasattr(tx1.message, 'instructions'):
                    tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
            except Exception:
                print('⚠️ Could not prepend compute budget instructions (library mismatch). Falling back to prioritizationFee only.')
    except Exception as e:
        print('⚠️ Unexpected error while attempting compute budget prepend:', e)

    tx = VersionedTransaction(tx1.message, [KEY])
    txId = http_client.send_raw_transaction(bytes(tx), TxOpts(skip_preflight=True)).value
    print(f"https://solscan.io/tx/{str(txId)}")
    return str(txId)



def get_time_range(days_back: int = 10):

    now = datetime.now()
    earlier = now - timedelta(days=days_back)
    time_to = int(now.timestamp())
    time_from = int(earlier.timestamp())
    return time_from, time_to


def round_down(value, decimals):
    factor = 10 ** decimals
    return math.floor(value * factor) / factor

def get_data(address, days_back_4_data, timeframe):
    time_from, time_to = get_time_range(days_back_4_data)

    # Check temp data first
    temp_file = f"temp_data/{address}_latest.csv"
    if os.path.exists(temp_file):
        print(f"📂 Moon Dev found cached data for {address[:4]}")
        return pd.read_csv(temp_file)

    url = f"https://public-api.birdeye.so/defi/ohlcv?address={address}&type={timeframe}&time_from={time_from}&time_to={time_to}"

    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        json_response = response.json()
        items = json_response.get('data', {}).get('items', [])

        processed_data = [{
            'Datetime (UTC)': datetime.utcfromtimestamp(item['unixTime']).strftime('%Y-%m-%d %H:%M:%S'),
            'Open': item['o'],
            'High': item['h'],
            'Low': item['l'],
            'Close': item['c'],
            'Volume': item['v']
        } for item in items]

        df = pd.DataFrame(processed_data)

        # Remove any rows with dates far in the future
        current_date = datetime.now()
        df['datetime_obj'] = pd.to_datetime(df['Datetime (UTC)'])
        df = df[df['datetime_obj'] <= current_date]
        df = df.drop('datetime_obj', axis=1)

        # Pad if needed
        if len(df) < 40:
            print("🌙 MoonDev Alert: Padding data to ensure minimum 40 rows for analysis! 🚀")
            rows_to_add = 40 - len(df)
            first_row_replicated = pd.concat([df.iloc[0:1]] * rows_to_add, ignore_index=True)
            df = pd.concat([first_row_replicated, df], ignore_index=True)

        print(f"📊 MoonDev's Data Analysis Ready! Processing {len(df)} candles... 🎯")

        # Always save to temp for current run
        df.to_csv(temp_file)
        print(f"🔄 Moon Dev cached data for {address[:4]}")

        # Calculate indicators
        df['MA20'] = ta.sma(df['Close'], length=20)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['MA40'] = ta.sma(df['Close'], length=40)

        df['Price_above_MA20'] = df['Close'] > df['MA20']
        df['Price_above_MA40'] = df['Close'] > df['MA40']
        df['MA20_above_MA40'] = df['MA20'] > df['MA40']

        return df
    else:
        print(f"❌ MoonDev Error: Failed to fetch data for address {address}. Status code: {response.status_code}")
        if response.status_code == 401:
            print("🔑 Check your BIRDEYE_API_KEY in .env file!")
        return pd.DataFrame()



def fetch_wallet_holdings_og(address):

    API_KEY = BIRDEYE_API_KEY  # Assume this is your API key; replace it with the actual one

    # Initialize an empty DataFrame
    df = pd.DataFrame(columns=['Mint Address', 'Amount', 'USD Value'])

    url = f"https://public-api.birdeye.so/v1/wallet/token_list?wallet={address}"
    headers = {"x-chain": "solana", "X-API-KEY": API_KEY}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        json_response = response.json()

        if 'data' in json_response and 'items' in json_response['data']:
            df = pd.DataFrame(json_response['data']['items'])
            df = df[['address', 'uiAmount', 'valueUsd']]
            df = df.rename(columns={'address': 'Mint Address', 'uiAmount': 'Amount', 'valueUsd': 'USD Value'})
            df = df.dropna()
            df = df[df['USD Value'] > 0.05]
        else:
            cprint("No data available in the response.", 'white', 'on_red')

    else:
        cprint(f"Failed to retrieve token list for {address}.", 'white', 'on_magenta')

    # Print the DataFrame if it's not empty
    if not df.empty:
        print(df)
        # Assuming cprint is a function you have for printing in color
        cprint(f'** Total USD balance is {df["USD Value"].sum()}', 'white', 'on_green')
        # Save the filtered DataFrame to a CSV file
        # TOKEN_PER_ADDY_CSV = 'filtered_wallet_holdings.csv'  # Define your CSV file name
        # df.to_csv(TOKEN_PER_ADDY_CSV, index=False)
    else:
        # If the DataFrame is empty, print a message or handle it as needed
        cprint("No wallet holdings to display.", 'white', 'on_red')

    return df

def fetch_wallet_token_single(address, token_mint_address):

    df = fetch_wallet_holdings_og(address)

    # filter by token mint address
    df = df[df['Mint Address'] == token_mint_address]

    return df


def token_price(address):
    url = f"https://public-api.birdeye.so/defi/price?address={address}"
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY}
    response = requests.get(url, headers=headers)
    price_data = response.json()

    print(price_data)

    if price_data['success']:
        return price_data['data']['value']
    else:
        return None
    
# price = token_price('2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv')
# print(price)
# time.sleep(897)


def get_position(token_mint_address):
    """
    Fetches the balance of a specific token given its mint address from a DataFrame.

    Parameters:
    - dataframe: A pandas DataFrame containing token balances with columns ['Mint Address', 'Amount'].
    - token_mint_address: The mint address of the token to find the balance for.

    Returns:
    - The balance of the specified token if found, otherwise a message indicating the token is not in the wallet.
    """
    dataframe = fetch_wallet_token_single(config.address, token_mint_address)

    #dataframe = pd.read_csv('data/token_per_addy.csv')

    print('-----------------')
    #print(dataframe)

    #print(dataframe)

    # Check if the DataFrame is empty
    if dataframe.empty:
        print("The DataFrame is empty. No positions to show.")
        return 0  # Indicating no balance found

    # Ensure 'Mint Address' column is treated as string for reliable comparison
    dataframe['Mint Address'] = dataframe['Mint Address'].astype(str)

    # Check if the token mint address exists in the DataFrame
    if dataframe['Mint Address'].isin([token_mint_address]).any():
        # Get the balance for the specified token
        balance = dataframe.loc[dataframe['Mint Address'] == token_mint_address, 'Amount'].iloc[0]
        #print(f"Balance for {token_mint_address[-4:]} token: {balance}")
        return balance
    else:
        # If the token mint address is not found in the DataFrame, return a message indicating so
        print("Token mint address not found in the wallet.")
        return 0  # Indicating no balance found


def get_decimals(token_mint_address):
    import requests
    import json
    # Solana Mainnet RPC endpoint
    url = "https://api.mainnet-beta.solana.com/"
    headers = {"Content-Type": "application/json"}

    # Request payload to fetch account information
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            token_mint_address,
            {
                "encoding": "jsonParsed"
            }
        ]
    })

    # Make the request to Solana RPC
    response = requests.post(url, headers=headers, data=payload)
    response_json = response.json()

    # Parse the response to extract the number of decimals
    decimals = response_json['result']['value']['data']['parsed']['info']['decimals']
    #print(f"Decimals for {token_mint_address[-4:]} token: {decimals}")

    return decimals

def pnl_close(token_mint_address):
    '''
    Staged exits (Moon Bags) — cleaned and hardened.
    '''

    print(f'checking pnl close to see if time to exit for {token_mint_address[:4]}...')

    # Get current position and price
    try:
        balance = get_position(token_mint_address)
    except Exception as e:
        print(f'❌ ERROR IN nice_funcs.py: {e}')
        return

    try:
        price = token_price(token_mint_address)
    except Exception as e:
        print(f'❌ ERROR IN nice_funcs.py: {e}')
        return

    try:
        decimals = get_decimals(token_mint_address)
    except Exception:
        decimals = 0

    usd_value = balance * price

    # Load or initialize position state
    positions = load_positions_state()
    pos_state = positions.get(token_mint_address, {})

    if balance > 0 and not pos_state.get('entry_price'):
        pos_state['entry_price'] = price
        pos_state['highest_price_seen'] = price
        pos_state['de_risked'] = False
        positions[token_mint_address] = pos_state
        save_positions_state(positions)

    # Update highest_price_seen
    if balance > 0 and pos_state:
        prev_high = pos_state.get('highest_price_seen', price)
        if price > prev_high:
            pos_state['highest_price_seen'] = price
            positions[token_mint_address] = pos_state
            save_positions_state(positions)

    # Defensive extraction of state values
    try:
        entry_price = float(pos_state.get('entry_price', price))
        de_risked = bool(pos_state.get('de_risked', False))
        highest_price = float(pos_state.get('highest_price_seen', price))
    except Exception:
        entry_price = price
        de_risked = False
        highest_price = price

    # Only attempt staged exits if we have a balance
    if balance > 0:
        # Stage 1: take 50% at 2x
        if (not de_risked) and price >= (entry_price * 2):
            sell_tokens = balance * 0.5
            sell_units = int(sell_tokens * (10 ** decimals))
            cprint(f"Stage1: 2x hit for {token_mint_address[:4]} — selling 50% ({sell_tokens} tokens / {sell_units} units)", 'white', 'on_magenta')
            try:
                market_sell(token_mint_address, sell_units, None, mode='standard')
                pos_state['de_risked'] = True
                positions[token_mint_address] = pos_state
                save_positions_state(positions)
                time.sleep(2)
                balance = get_position(token_mint_address)
                price = token_price(token_mint_address)
                usd_value = balance * price
            except Exception as e:
                print(f'❌ ERROR IN nice_funcs.py: {e}')

        # Stage 2: if already de_risked and price drops 20% from peak
        elif de_risked:
            highest_price = float(pos_state.get('highest_price_seen', price))
            if price <= (highest_price * 0.80):
                try:
                    balance = get_position(token_mint_address)
                except Exception as e:
                    print(f'❌ ERROR IN nice_funcs.py: {e}')
                    balance = 0

                sell_tokens = balance
                sell_units = int(sell_tokens * (10 ** decimals))
                cprint(f"Stage2: Moon bag trailing triggered for {token_mint_address[:4]} — selling remaining {sell_tokens} tokens ({sell_units} units) in panic mode", 'white', 'on_magenta')
                try:
                    market_sell(token_mint_address, sell_units, None, mode='panic')
                    if token_mint_address in positions:
                        positions.pop(token_mint_address, None)
                        save_positions_state(positions)
                    time.sleep(2)
                    balance = get_position(token_mint_address)
                    price = token_price(token_mint_address)
                    usd_value = balance * price
                    with open('dont_overtrade.txt', 'a') as file:
                        file.write(token_mint_address + '\n')
                except Exception as e:
                    print(f'❌ ERROR IN nice_funcs.py: {e}')

    # Fallback TP/SL using config values
    tp = config.sell_at_multiple * config.USDC_SIZE
    sl = ((1 + config.stop_loss_percentage) * config.USDC_SIZE)

    try:
        sell_size = int(balance * (10 ** decimals))
    except Exception:
        sell_size = 0

    # TP loop
    while usd_value > tp and sell_size > 0:
        cprint(
            f'for {token_mint_address[:4]} value is {usd_value} and tp is {tp} so closing...',
            'white',
            'on_green',
        )
        try:
            market_sell(token_mint_address, sell_size)
            cprint(f'just made an order {token_mint_address[:4]} selling {sell_size} ...', 'white', 'on_green')
            time.sleep(2)
        except Exception as e:
            print(f'❌ ERROR IN nice_funcs.py: {e}')
            cprint('order error.. trying again', 'white', 'on_red')
            time.sleep(2)

        balance = get_position(token_mint_address)
        price = token_price(token_mint_address)
        usd_value = balance * price
        try:
            sell_size = int(balance * (10 ** decimals))
        except Exception:
            sell_size = 0

    # SL loop
    if usd_value != 0:
        while usd_value < sl and usd_value > 0 and sell_size > 0:
            cprint(f'for {token_mint_address[:4]} value is {usd_value} and sl is {sl} so closing as a loss...', 'white', 'on_blue')
            try:
                market_sell(token_mint_address, sell_size, None, mode='panic')
                cprint(f'just made an order {token_mint_address[:4]} selling {sell_size} ...', 'white', 'on_blue')
                time.sleep(1)
            except Exception as e:
                print(f'❌ ERROR IN nice_funcs.py: {e}')
                cprint('order error.. trying again', 'white', 'on_red')

            balance = get_position(token_mint_address)
            price = token_price(token_mint_address)
            usd_value = balance * price
            tp = config.sell_at_multiple * config.USDC_SIZE
            sl = ((1 + config.stop_loss_percentage) * config.USDC_SIZE)
            try:
                sell_size = int(balance * (10 ** decimals))
            except Exception:
                sell_size = 0

            if usd_value == 0:
                try:
                    with open('dont_overtrade.txt', 'a') as file:
                        file.write(token_mint_address + '\n')
                except Exception:
                    pass
                break
    else:
        print(f'for {token_mint_address[:4]} value is {usd_value} and tp is {tp} so not closing...')

def chunk_kill(token_mint_address, max_usd_order_size, slippage):
    """Kill a position in chunks"""
    cprint("\n🔪 Moon Dev's AI Agent initiating position exit...", "white", "on_cyan")
    
    try:
        # Get current position using address from config
        df = fetch_wallet_token_single(config.address, token_mint_address)
        if df.empty:
            cprint("❌ No position found to exit", "white", "on_red")
            return
            
        # Get current token amount and value
        token_amount = float(df['Amount'].iloc[0])
        current_usd_value = float(df['USD Value'].iloc[0])
        
        # Get token decimals
        decimals = get_decimals(token_mint_address)
        
        cprint(f"📊 Initial position: {token_amount:.2f} tokens (${current_usd_value:.2f})", "white", "on_cyan")
        
        while current_usd_value > 0.1:  # Keep going until position is essentially zero
            # Calculate chunk size based on current position
            chunk_size = token_amount / 3  # Split remaining into 3 chunks
            cprint(f"\n🔄 Splitting remaining position into chunks of {chunk_size:.2f} tokens", "white", "on_cyan")
            
            # Execute sell orders in chunks
            for i in range(3):
                try:
                    cprint(f"\n💫 Executing sell chunk {i+1}/3...", "white", "on_cyan")
                    sell_size = int(chunk_size * 10**decimals)
                    market_sell(token_mint_address, sell_size, slippage, mode='panic')
                    cprint(f"✅ Sell chunk {i+1}/3 complete", "white", "on_green")
                    time.sleep(2)  # Small delay between chunks
                except Exception as e:
                    cprint(f"❌ Error in sell chunk: {str(e)}", "white", "on_red")
            
            # Check remaining position
            time.sleep(5)  # Wait for blockchain to update
            df = fetch_wallet_token_single(config.address, token_mint_address)
            if df.empty:
                cprint("\n✨ Position successfully closed!", "white", "on_green")
                return
                
            # Update position size for next iteration
            token_amount = float(df['Amount'].iloc[0])
            current_usd_value = float(df['USD Value'].iloc[0])
            cprint(f"\n📊 Remaining position: {token_amount:.2f} tokens (${current_usd_value:.2f})", "white", "on_cyan")
            
            if current_usd_value > 0.1:
                cprint("🔄 Position still open - continuing to close...", "white", "on_cyan")
                time.sleep(2)
            
        cprint("\n✨ Position successfully closed!", "white", "on_green")
        
    except Exception as e:
        cprint(f"❌ Error during position exit: {str(e)}", "white", "on_red")

def sell_token(token_mint_address, amount, slippage):
    """Sell a token"""
    try:
        cprint(f"📉 Selling {amount:.2f} tokens...", "white", "on_cyan")
        # Your existing sell logic here
        print(f"just made an order {token_mint_address[:4]} selling {int(amount)} ...")
    except Exception as e:
        cprint(f"❌ Error selling token: {str(e)}", "white", "on_red")

def kill_switch(token_mint_address):

    ''' this function closes the position in full

    if the usd_size > 10k then it will chunk in 10k orders
    '''

    # if time is on the 5 minute do the balance check, if not grab from data/current_position.csv
    balance = get_position(token_mint_address)

    # get current price of token
    price = token_price(token_mint_address)
    price = float(price)

    usd_value = balance * price

    if usd_value < 10000:
        sell_size = balance
    else:
        sell_size = 10000/price

    # compute TP (used later by loop) - avoid unused-assignment here
    # tp value will be recomputed inside the TP loop when needed

    # round to 2 decimals
    sell_size = round_down(sell_size, 2)
    decimals = 0
    decimals = get_decimals(token_mint_address)
    sell_size = int(sell_size * 10 **decimals)

    #print(f'bal: {balance} price: {price} usdVal: {usd_value} TP: {tp} sell size: {sell_size} decimals: {decimals}')

    while usd_value > 0:


# 100 selling 70% ...... selling 30 left
        #print(f'for {token_mint_address[-4:]} closing position cause exit all positions is set to {EXIT_ALL_POSITIONS} and value is {usd_value} and tp is {tp} so closing...')
        try:

            market_sell(token_mint_address, sell_size, None, mode='panic')
            cprint(f'just made an order {token_mint_address[:4]} selling {sell_size} ...', 'white', 'on_blue')
            time.sleep(1)
            market_sell(token_mint_address, sell_size, None, mode='panic')
            cprint(f'just made an order {token_mint_address[:4]} selling {sell_size} ...', 'white', 'on_blue')
            time.sleep(1)
            market_sell(token_mint_address, sell_size, None, mode='panic')
            cprint(f'just made an order {token_mint_address[:4]} selling {sell_size} ...', 'white', 'on_blue')
            time.sleep(15)

        except Exception as e:
            print(f'❌ ERROR IN nice_funcs.py: {e}')
            cprint('order error.. trying again', 'white', 'on_red')
            # time.sleep(7)

        balance = get_position(token_mint_address)
        price = token_price(token_mint_address)
        usd_value = balance * price

        if usd_value < 10000:
            sell_size = balance
        else:
            sell_size = 10000 / price

        # down downwards to 2 decimals
        sell_size = round_down(sell_size, 2)

        decimals = 0
        decimals = get_decimals(token_mint_address)
        #print(f'xxxxxxxxx for {token_mint_address[-4:]} decimals is {decimals}')
        sell_size = int(sell_size * 10 ** decimals)
        print(f'balance is {balance} and usd_value is {usd_value} EXIT ALL POSITIONS TRUE and sell_size is {sell_size} decimals is {decimals}')

    else:
        print(f'for {token_mint_address[:4]} value is {usd_value} ')
        #time.sleep(10)

    print('closing position in full...')

def close_all_positions():

    # get all positions
    open_positions = fetch_wallet_holdings_og(config.address)

    # loop through all positions and close them getting the mint address from Mint Address column
    for index, row in open_positions.iterrows():
        token_mint_address = row['Mint Address']

        # Check if the current token mint address is the USDC contract address
        cprint(f'this is the token mint address {token_mint_address} this is don not trade list {config.dont_trade_list}', 'white', 'on_magenta')
        if token_mint_address in config.dont_trade_list:
            print(f'Skipping kill switch for USDC contract at {token_mint_address}')
            continue  # Skip the rest of the loop for this iteration

        print(f'Closing position for {token_mint_address}...')
        kill_switch(token_mint_address)

def delete_dont_overtrade_file():
    if os.path.exists('dont_overtrade.txt'):
        os.remove('dont_overtrade.txt')
        print('dont_overtrade.txt has been deleted')
    else:
        print('The file does not exist')

def supply_demand_zones(token_address, timeframe, limit):

    print('starting moons supply and demand zone calculations..')

    sd_df = pd.DataFrame()

    time_from, time_to = get_time_range()

    df = get_data(token_address, time_from, time_to, timeframe)

    # only keep the data for as many bars as limit says
    df = df[-limit:]
    #print(df)
    #time.sleep(100)

    # Calculate support and resistance, excluding the last two rows for the calculation
    if len(df) > 2:  # Check if DataFrame has more than 2 rows to avoid errors
        df['support'] = df[:-2]['Close'].min()
        df['resis'] = df[:-2]['Close'].max()
    else:  # If DataFrame has 2 or fewer rows, use the available 'close' prices for calculation
        df['support'] = df['Close'].min()
        df['resis'] = df['Close'].max()

    supp = df.iloc[-1]['support']
    resis = df.iloc[-1]['resis']
    #print(f'this is moons support for 1h {supp_1h} this is resis: {resis_1h}')

    df['supp_lo'] = df[:-2]['Low'].min()
    supp_lo = df.iloc[-1]['supp_lo']

    df['res_hi'] = df[:-2]['High'].max()
    res_hi = df.iloc[-1]['res_hi']

    #print(df)


    sd_df['dz'] = [supp_lo, supp]
    sd_df['sz'] = [res_hi, resis]

    print('here are moons supply and demand zones')
    #print(sd_df)

    return sd_df


def elegant_entry(symbol, buy_under):

    pos = get_position(symbol)
    price = token_price(symbol)
    pos_usd = pos * price
    size_needed = config.usd_size - pos_usd
    if size_needed > config.max_usd_order_size:
        chunk_size = config.max_usd_order_size
    else:
        chunk_size = size_needed

    chunk_size = int(chunk_size * 10**6)
    chunk_size = str(chunk_size)

    print(f'chunk_size: {chunk_size}')

    if pos_usd > (.97 * config.usd_size):
        print('position filled')
        time.sleep(config.tx_sleep)

    # add debug prints for next while
    print(f'position: {round(pos,2)} price: {round(price,8)} pos_usd: ${round(pos_usd,2)}')
    print(f'buy_under: {buy_under}')
    while pos_usd < (.97 * config.usd_size) and (price < buy_under):

        print(f'position: {round(pos,2)} price: {round(price,8)} pos_usd: ${round(pos_usd,2)}')

        try:

            for i in range(config.orders_per_open):
                market_buy(symbol, chunk_size, config.slippage)
                # cprint green background black text
                cprint(f'chunk buy submitted of {symbol[:4]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                time.sleep(1)

            time.sleep(config.tx_sleep)

            pos = get_position(symbol)
            price = token_price(symbol)
            pos_usd = pos * price
            size_needed = config.usd_size - pos_usd
            if size_needed > config.max_usd_order_size:
                chunk_size = config.max_usd_order_size
            else:
                chunk_size = size_needed
            chunk_size = int(chunk_size * 10**6)
            chunk_size = str(chunk_size)
        except Exception as e:
            print(f'❌ ERROR IN nice_funcs.py: {e}')
            try:
                cprint('trying again to make the order in 30 seconds.....', 'light_blue', 'on_light_magenta')
                time.sleep(30)
                for i in range(config.orders_per_open):
                    market_buy(symbol, chunk_size, config.slippage)
                    cprint(f'chunk buy submitted of {symbol[:4]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                    time.sleep(1)

                time.sleep(config.tx_sleep)
                pos = get_position(symbol)
                price = token_price(symbol)
                pos_usd = pos * price
                size_needed = config.usd_size - pos_usd
                if size_needed > config.max_usd_order_size:
                    chunk_size = config.max_usd_order_size
                else:
                    chunk_size = size_needed
                chunk_size = int(chunk_size * 10**6)
                chunk_size = str(chunk_size)

            except Exception as e:
                print(f'❌ ERROR IN nice_funcs.py: {e}')
                cprint('Final Error in the buy, restart needed', 'white', 'on_red')
                time.sleep(10)
                break

        pos = get_position(symbol)
        price = token_price(symbol)
        pos_usd = pos * price
        size_needed = config.usd_size - pos_usd
        if size_needed > config.max_usd_order_size:
            chunk_size = config.max_usd_order_size
        else:
            chunk_size = size_needed
        chunk_size = int(chunk_size * 10**6)
        chunk_size = str(chunk_size)


# like the elegant entry but for breakout so its looking for price > BREAKOUT_PRICE
def breakout_entry(symbol, BREAKOUT_PRICE):

    pos = get_position(symbol)
    price = token_price(symbol)
    price = float(price)
    pos_usd = pos * price
    size_needed = config.usd_size - pos_usd
    if size_needed > config.max_usd_order_size:
        chunk_size = config.max_usd_order_size
    else:
        chunk_size = size_needed

    chunk_size = int(chunk_size * 10**6)
    chunk_size = str(chunk_size)

    print(f'chunk_size: {chunk_size}')

    if pos_usd > (.97 * config.usd_size):
        print('position filled')
        time.sleep(config.tx_sleep)

    # add debug prints for next while
    print(f'position: {round(pos,2)} price: {round(price,8)} pos_usd: ${round(pos_usd,2)}')
    print(f'breakoutpurce: {BREAKOUT_PRICE}')
    while pos_usd < (.97 * config.usd_size) and (price > BREAKOUT_PRICE):

        print(f'position: {round(pos,2)} price: {round(price,8)} pos_usd: ${round(pos_usd,2)}')

        # for i in range(orders_per_open):
        #     market_buy(symbol, chunk_size, slippage)
        #     # cprint green background black text
        #     cprint(f'chunk buy submitted of {symbol[-4:]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
        #     time.sleep(1)

        # time.sleep(tx_sleep)

        # pos = get_position(symbol)
        # price = token_price(symbol)
        # pos_usd = pos * price
        # size_needed = usd_size - pos_usd
        # if size_needed > max_usd_order_size: chunk_size = max_usd_order_size
        # else: chunk_size = size_needed
        # chunk_size = int(chunk_size * 10**6)
        # chunk_size = str(chunk_size)

        try:

            for i in range(config.orders_per_open):
                market_buy(symbol, chunk_size, config.slippage)
                # cprint green background black text
                cprint(f'chunk buy submitted of {symbol[:4]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                time.sleep(1)
            time.sleep(config.tx_sleep)

            pos = get_position(symbol)
            price = token_price(symbol)
            pos_usd = pos * price
            size_needed = config.usd_size - pos_usd
            if size_needed > config.max_usd_order_size:
                chunk_size = config.max_usd_order_size
            else:
                chunk_size = size_needed
            chunk_size = int(chunk_size * 10**6)
            chunk_size = str(chunk_size)

        except Exception as e:
            print(f'❌ ERROR IN nice_funcs.py: {e}')
            try:
                cprint('trying again to make the order in 30 seconds.....', 'light_blue', 'on_light_magenta')
                time.sleep(30)
                for i in range(config.orders_per_open):
                    market_buy(symbol, chunk_size, config.slippage)
                    cprint(f'chunk buy submitted of {symbol[:4]} sz: {chunk_size} you my dawg moon dev', 'white', 'on_blue')
                    time.sleep(1)

                time.sleep(config.tx_sleep)
                pos = get_position(symbol)
                price = token_price(symbol)
                pos_usd = pos * price
                size_needed = config.usd_size - pos_usd
                if size_needed > config.max_usd_order_size:
                    chunk_size = config.max_usd_order_size
                else:
                    chunk_size = size_needed
                chunk_size = int(chunk_size * 10**6)
                chunk_size = str(chunk_size)

            except Exception as e:
                print(f'❌ ERROR IN nice_funcs.py: {e}')
                cprint('Final Error in the buy, restart needed', 'white', 'on_red')
                time.sleep(10)
                break

        pos = get_position(symbol)
        price = token_price(symbol)
        pos_usd = pos * price
        size_needed = config.usd_size - pos_usd
        if size_needed > config.max_usd_order_size:
            chunk_size = config.max_usd_order_size
        else:
            chunk_size = size_needed
        chunk_size = int(chunk_size * 10**6)
        chunk_size = str(chunk_size)



def ai_entry(symbol, amount):
    """AI agent entry function for Moon Dev's trading system 🤖"""
    cprint("🤖 Moon Dev's AI Trading Agent initiating position entry...", "white", "on_blue")
    
    # amount passed in is the target allocation (up to 30% of usd_size)
    target_size = amount  # This could be up to $3 (30% of $10)
    
    pos = get_position(symbol)
    price = token_price(symbol)
    pos_usd = pos * price
    
    cprint(f"🎯 Target allocation: ${target_size:.2f} USD (max 30% of ${config.usd_size})", "white", "on_blue")
    cprint(f"📊 Current position: ${pos_usd:.2f} USD", "white", "on_blue")
    
    # Check if we're already at or above target
    if pos_usd >= (target_size * 0.97):
        cprint("✋ Position already at or above target size!", "white", "on_blue")
        return
        
    # Calculate how much more we need to buy
    size_needed = target_size - pos_usd
    if size_needed <= 0:
        cprint("🛑 No additional size needed", "white", "on_blue")
        return
        
    # For order execution, we'll chunk into max_usd_order_size pieces
    if size_needed > config.max_usd_order_size:
        chunk_size = config.max_usd_order_size
    else:
        chunk_size = size_needed

    chunk_size = int(chunk_size * 10**6)
    chunk_size = str(chunk_size)
    
    cprint(f"💫 Entry chunk size: {chunk_size} (chunking ${size_needed:.2f} into ${config.max_usd_order_size:.2f} orders)", "white", "on_blue")

    while pos_usd < (target_size * 0.97):
        cprint(f"🤖 AI Agent executing entry for {symbol[:8]}...", "white", "on_blue")
        print(f"Position: {round(pos,2)} | Price: {round(price,8)} | USD Value: ${round(pos_usd,2)}")

        try:
            for i in range(config.orders_per_open):
                market_buy(symbol, chunk_size, config.slippage)
                cprint(f"🚀 AI Agent placed order {i+1}/{config.orders_per_open} for {symbol[:8]}", "white", "on_blue")
                time.sleep(1)

            time.sleep(config.tx_sleep)
            
            # Update position info
            pos = get_position(symbol)
            price = token_price(symbol)
            pos_usd = pos * price
            
            # Break if we're at or above target
            if pos_usd >= (target_size * 0.97):
                break
                
            # Recalculate needed size
            size_needed = target_size - pos_usd
            if size_needed <= 0:
                break
                
            # Determine next chunk size
            if size_needed > config.max_usd_order_size:
                chunk_size = config.max_usd_order_size
            else:
                chunk_size = size_needed
            chunk_size = int(chunk_size * 10**6)
            chunk_size = str(chunk_size)
        except Exception as e:
            print(f'❌ ERROR IN nice_funcs.py: {e}')
            try:
                cprint("🔄 AI Agent retrying order in 30 seconds...", "white", "on_blue")
                time.sleep(30)
                for i in range(config.orders_per_open):
                    market_buy(symbol, chunk_size, config.slippage)
                    cprint(f"🚀 AI Agent retry order {i+1}/{config.orders_per_open} for {symbol[:8]}", "white", "on_blue")
                    time.sleep(1)

                time.sleep(config.tx_sleep)
                pos = get_position(symbol)
                price = token_price(symbol)
                pos_usd = pos * price
                
                if pos_usd >= (target_size * 0.97):
                    break
                    
                size_needed = target_size - pos_usd
                if size_needed <= 0:
                    break
                    
                if size_needed > config.max_usd_order_size:
                    chunk_size = config.max_usd_order_size
                else:
                    chunk_size = size_needed
                chunk_size = int(chunk_size * 10**6)
                chunk_size = str(chunk_size)

            except Exception as e:
                print(f'❌ ERROR IN nice_funcs.py: {e}')
                cprint("❌ AI Agent encountered critical error, manual intervention needed", "white", "on_red")
                return

    cprint("✨ AI Agent completed position entry", "white", "on_blue")

def get_token_balance_usd(token_mint_address):
    """Get the USD value of a token position for Moon Dev's wallet 🌙"""
    try:
        # Get the position data using existing function
        df = fetch_wallet_token_single(config.address, token_mint_address)  # Using address from config

        if df.empty:
            print(f"🔍 No position found for {token_mint_address[:8]}")
            return 0.0

        # Get the USD Value from the dataframe
        usd_value = df['USD Value'].iloc[0]
        return float(usd_value)

    except Exception as e:
        print(f"❌ Error getting token balance: {str(e)}")
        return 0.0
