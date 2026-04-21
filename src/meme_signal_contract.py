"""Source-aware trust helpers for normalized meme signal tape rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SignalSourceContract:
    """Declares which canonical fields a source observes vs estimates."""

    source: str
    family: str
    observed_fields: frozenset[str]
    estimated_fields: frozenset[str]
    missing_fields: frozenset[str]


DEFAULT_SOURCE_CONTRACT = SignalSourceContract(
    source="unknown",
    family="unknown",
    observed_fields=frozenset(),
    estimated_fields=frozenset(),
    missing_fields=frozenset(),
)


SOURCE_CONTRACTS: dict[str, SignalSourceContract] = {
    "ws_logs": SignalSourceContract(
        source="ws_logs",
        family="flow_discovery",
        observed_fields=frozenset(
            {
                "hits",
                "buys",
                "sells",
                "unique_buyers",
                "net_sol_in",
                "buy_accel",
                "buy_max_sol",
                "t_first_sell_s",
                "top_buyer_share",
                "buyer_wallets",
                "buyer_wallet_shares",
            }
        ),
        estimated_fields=frozenset(),
        missing_fields=frozenset(
            {
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
            }
        ),
    ),
    "dex_mover": SignalSourceContract(
        source="dex_mover",
        family="market_state",
        observed_fields=frozenset(
            {
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
                "hits",
                "buys",
                "sells",
                "net_sol_in",
                "buy_accel",
                "buy_max_sol",
            }
        ),
        estimated_fields=frozenset({"unique_buyers", "top_buyer_share"}),
        missing_fields=frozenset({"buyer_wallets", "buyer_wallet_shares"}),
    ),
    "ds_sidecar": SignalSourceContract(
        source="ds_sidecar",
        family="market_state",
        observed_fields=frozenset(
            {
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
                "hits",
                "buys",
                "sells",
                "net_sol_in",
                "buy_accel",
                "buy_max_sol",
            }
        ),
        estimated_fields=frozenset({"unique_buyers", "top_buyer_share"}),
        missing_fields=frozenset({"buyer_wallets", "buyer_wallet_shares"}),
    ),
    "wallet_outlier": SignalSourceContract(
        source="wallet_outlier",
        family="wallet_overlay",
        observed_fields=frozenset(
            {
                "hits",
                "buys",
                "sells",
                "unique_buyers",
                "net_sol_in",
                "top_buyer_share",
                "buy_accel",
                "buy_max_sol",
                "t_first_sell_s",
                "buyer_wallets",
                "buyer_wallet_shares",
                "wallet_alpha_score",
            }
        ),
        estimated_fields=frozenset(),
        missing_fields=frozenset(
            {
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
            }
        ),
    ),
}


def signal_source(metrics: Mapping[str, Any] | None, fallback: str | None = None) -> str:
    if isinstance(metrics, Mapping):
        raw = metrics.get("source")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return str(fallback or "").strip().lower()


def get_signal_source_contract(source: str | None) -> SignalSourceContract:
    src = str(source or "").strip().lower()
    return SOURCE_CONTRACTS.get(src, DEFAULT_SOURCE_CONTRACT)


def signal_source_family(source: str | None) -> str:
    return get_signal_source_contract(source).family


def signal_field_status(
    metrics: Mapping[str, Any] | None,
    field: str,
    source: str | None = None,
) -> str:
    """Return observed / estimated / missing / unknown for a canonical field."""
    m = metrics if isinstance(metrics, Mapping) else {}
    contract = get_signal_source_contract(source or signal_source(m))
    estimate_flag = f"{field}_estimated"
    has_value = field in m and m.get(field) is not None

    if bool(m.get(estimate_flag)):
        return "estimated" if has_value else "missing"
    if field in contract.estimated_fields:
        return "estimated" if has_value else "missing"
    if field in contract.observed_fields:
        return "observed" if has_value else "missing"
    if field in contract.missing_fields:
        return "missing"
    if has_value:
        return "observed"
    return "unknown"


def signal_field_value(
    metrics: Mapping[str, Any] | None,
    field: str,
    *,
    source: str | None = None,
    allow_estimated: bool = True,
    default: Any = None,
) -> Any:
    status = signal_field_status(metrics, field, source)
    if status == "missing":
        return default
    if status == "estimated" and not allow_estimated:
        return default
    if not isinstance(metrics, Mapping):
        return default
    return metrics.get(field, default)


def signal_contract_snapshot(
    metrics: Mapping[str, Any] | None,
    *,
    source: str | None = None,
    fields: Iterable[str],
) -> dict[str, str]:
    src = source or signal_source(metrics)
    return {field: signal_field_status(metrics, field, src) for field in fields}
