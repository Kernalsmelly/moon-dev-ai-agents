"""
Polymarket Micro-Edge Module

This module provides tools for finding alpha in long-tail Polymarket markets
that big bots ignore. Key components:

- market_classifier: Niche scoring (0-10) for market targeting
- liquidity_analyzer: Low-liquidity market finder
- resolution_timer: Near-resolution edge detection
- correlation_engine: Cross-market arbitrage detection
- gamma_api: Fetch all markets from Polymarket Gamma API
- micro_store: SQLite persistence for signals and positions
"""

# Import each component with graceful fallback
try:
    from .market_classifier import NicheClassifier
except ImportError:
    NicheClassifier = None

try:
    from .liquidity_analyzer import LiquidityAnalyzer
except ImportError:
    LiquidityAnalyzer = None

try:
    from .resolution_timer import ResolutionTimer
except ImportError:
    ResolutionTimer = None

try:
    from .correlation_engine import CorrelationEngine
except ImportError:
    CorrelationEngine = None

try:
    from .gamma_api import GammaAPI
except ImportError:
    GammaAPI = None

try:
    from .micro_store import MicroEdgeStore, get_store
except ImportError:
    MicroEdgeStore = None
    get_store = None

__all__ = [
    'NicheClassifier',
    'LiquidityAnalyzer',
    'ResolutionTimer',
    'CorrelationEngine',
    'GammaAPI',
    'MicroEdgeStore',
    'get_store',
]
