from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from src.solana.rpc_pool import RpcPool, RpcError


@dataclass(frozen=True)
class SplMintInfo:
    mint: str
    decimals: int
    supply_raw: int
    is_initialized: bool
    mint_authority_present: bool
    freeze_authority_present: bool


def _parse_mint_account(data: bytes, mint: str) -> SplMintInfo | None:
    # SPL Token Mint layout (82 bytes):
    #   mintAuthorityOption: u32 (LE)
    #   mintAuthority: [32]
    #   supply: u64 (LE)
    #   decimals: u8
    #   isInitialized: u8
    #   freezeAuthorityOption: u32 (LE)
    #   freezeAuthority: [32]
    if not isinstance(data, (bytes, bytearray)) or len(data) < 82:
        return None

    def u32(off: int) -> int:
        return int.from_bytes(data[off : off + 4], "little", signed=False)

    def u64(off: int) -> int:
        return int.from_bytes(data[off : off + 8], "little", signed=False)

    mint_opt = u32(0)
    mint_auth_raw = bytes(data[4:36])
    supply = u64(36)
    decimals = int(data[44])
    is_init = bool(int(data[45]))
    freeze_opt = u32(46)
    freeze_auth_raw = bytes(data[50:82])

    # We only need presence to reject. Avoid extra deps for base58 conversion.
    mint_auth_present = (mint_opt != 0)
    freeze_auth_present = (freeze_opt != 0)

    return SplMintInfo(
        mint=mint,
        decimals=decimals,
        supply_raw=supply,
        is_initialized=is_init,
        mint_authority_present=mint_auth_present,
        freeze_authority_present=freeze_auth_present,
    )


def fetch_spl_mint_info(pool: RpcPool, mint: str) -> SplMintInfo | None:
    """Fetch SPL mint authority/freeze authority directly from RPC.

    This avoids Birdeye dependency for a critical anti-rug check.
    """
    try:
        res = pool.call("getAccountInfo", [mint, {"encoding": "base64"}])
    except RpcError:
        return None
    except Exception:
        return None

    try:
        value = (res or {}).get("value") if isinstance(res, dict) else None
        if not isinstance(value, dict):
            return None
        data = value.get("data")
        if not (isinstance(data, list) and len(data) >= 1 and isinstance(data[0], str)):
            return None
        raw = base64.b64decode(data[0])
        return _parse_mint_account(raw, mint=mint)
    except Exception:
        return None
