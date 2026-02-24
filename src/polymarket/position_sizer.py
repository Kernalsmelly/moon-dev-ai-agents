"""
Kelly Criterion Position Sizer for Polymarket

Calculates optimal position size based on:
- Edge percentage (how much better our estimate vs market)
- Confidence in the signal (data source reliability)
- Available capital

Kelly Formula: f* = (p * b - q) / b
Where:
- f* = fraction of bankroll to bet
- p = probability of winning (our estimate)
- q = probability of losing (1 - p)
- b = net odds (profit / stake if we win)

For Polymarket:
- Buy YES at $0.40, win = $1.00, profit = $0.60
- Net odds b = 0.60 / 0.40 = 1.5

We use "fractional Kelly" (typically 25-50%) to reduce variance.
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class PositionSize:
    """Result of position sizing calculation."""
    amount_usd: float
    kelly_fraction: float
    full_kelly_pct: float
    adjusted_kelly_pct: float
    reasoning: str


class KellyPositionSizer:
    """
    Kelly Criterion position sizing with safety limits.

    Features:
    - Fractional Kelly (default 25%) to reduce variance
    - Maximum position size cap
    - Minimum position size floor
    - Confidence weighting
    """

    def __init__(
        self,
        total_capital: float = 250.0,
        kelly_fraction: float = 0.25,  # Use 25% Kelly (conservative)
        max_position_pct: float = 0.10,  # Max 10% of capital per position
        min_position_usd: float = 5.0,   # Minimum $5 position
        max_position_usd: float = 25.0,  # Maximum $25 position
    ):
        self.total_capital = total_capital
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct
        self.min_position_usd = min_position_usd
        self.max_position_usd = max_position_usd

    def calculate_position_size(
        self,
        entry_price: float,
        edge_pct: float,
        confidence: float = 0.5,
        side: str = 'YES',
    ) -> PositionSize:
        """
        Calculate optimal position size using Kelly criterion.

        Args:
            entry_price: Price to enter (0-1)
            edge_pct: Our edge percentage (e.g., 15.0 for 15%)
            confidence: Confidence in our estimate (0-1)
            side: 'YES' or 'NO'

        Returns:
            PositionSize with amount and reasoning
        """
        # Validate inputs
        if entry_price <= 0 or entry_price >= 1:
            return PositionSize(
                amount_usd=self.min_position_usd,
                kelly_fraction=0,
                full_kelly_pct=0,
                adjusted_kelly_pct=0,
                reasoning="Invalid entry price"
            )

        # Calculate our estimated probability
        # If buying YES at $0.40 with 15% edge, we think true prob = 0.40 + 0.15*0.40 = 0.46
        # Actually, edge_pct is (our_prob - market_price) / entry_price * 100
        # So our_prob = market_price + (edge_pct/100) * entry_price
        if side == 'YES':
            market_prob = entry_price
            our_prob = min(0.95, market_prob * (1 + edge_pct / 100))
        else:
            # For NO, entry_price is what we pay for NO
            market_prob = 1 - entry_price  # Market's implied YES probability
            our_prob = max(0.05, market_prob * (1 - edge_pct / 100))
            # Convert back: if our YES prob is lower, our NO prob is higher

        # Kelly formula
        # For YES bet: win $1-entry_price if correct, lose entry_price if wrong
        # b = (1 - entry_price) / entry_price = net odds
        # p = our_prob, q = 1 - our_prob

        if side == 'YES':
            p = our_prob
            b = (1 - entry_price) / entry_price
        else:
            # For NO bet at entry_price, we win (1 - (1-entry_price))/entry_price
            # Actually for NO: we pay entry_price, get $1 if NO wins
            p = 1 - our_prob  # Probability of NO winning
            b = (1 - entry_price) / entry_price

        q = 1 - p

        # Kelly fraction
        kelly = (p * b - q) / b if b > 0 else 0

        # Can be negative if no edge - don't bet
        if kelly <= 0:
            return PositionSize(
                amount_usd=0,
                kelly_fraction=0,
                full_kelly_pct=0,
                adjusted_kelly_pct=0,
                reasoning="No positive edge detected"
            )

        # Apply fractional Kelly
        adjusted_kelly = kelly * self.kelly_fraction

        # Apply confidence weighting
        # Higher confidence = closer to full fractional Kelly
        # Lower confidence = reduce position further
        confidence_adjusted = adjusted_kelly * (0.5 + confidence * 0.5)

        # Calculate dollar amount
        position_usd = self.total_capital * confidence_adjusted

        # Apply limits
        max_by_pct = self.total_capital * self.max_position_pct
        position_usd = min(position_usd, max_by_pct, self.max_position_usd)
        position_usd = max(position_usd, self.min_position_usd)

        # Round to reasonable amount
        position_usd = round(position_usd, 2)

        return PositionSize(
            amount_usd=position_usd,
            kelly_fraction=self.kelly_fraction,
            full_kelly_pct=round(kelly * 100, 2),
            adjusted_kelly_pct=round(confidence_adjusted * 100, 2),
            reasoning=f"Kelly: {kelly*100:.1f}% -> {confidence_adjusted*100:.1f}% (conf={confidence:.0%})"
        )

    def size_signal(self, signal: Dict) -> Dict:
        """
        Add position sizing to a signal dict.

        Args:
            signal: Signal dict with entry_price, edge_pct, confidence, side

        Returns:
            Signal dict with added 'position_size' field
        """
        size = self.calculate_position_size(
            entry_price=signal.get('entry_price', 0.5),
            edge_pct=signal.get('edge_pct', 0),
            confidence=signal.get('confidence', 0.5),
            side=signal.get('side', 'YES'),
        )

        signal['position_size_usd'] = size.amount_usd
        signal['kelly_info'] = {
            'full_kelly_pct': size.full_kelly_pct,
            'adjusted_kelly_pct': size.adjusted_kelly_pct,
            'reasoning': size.reasoning,
        }

        return signal

    def size_signals(self, signals: list) -> list:
        """Add position sizing to a list of signals."""
        return [self.size_signal(s) for s in signals]


# Singleton instance
_sizer: Optional[KellyPositionSizer] = None


def get_sizer(total_capital: float = 250.0) -> KellyPositionSizer:
    """Get singleton position sizer."""
    global _sizer
    if _sizer is None:
        _sizer = KellyPositionSizer(total_capital=total_capital)
    return _sizer


def calculate_position(
    entry_price: float,
    edge_pct: float,
    confidence: float = 0.5,
    side: str = 'YES',
    total_capital: float = 250.0,
) -> PositionSize:
    """Convenience function for one-off calculations."""
    sizer = KellyPositionSizer(total_capital=total_capital)
    return sizer.calculate_position_size(entry_price, edge_pct, confidence, side)


# Example usage and testing
if __name__ == '__main__':
    sizer = KellyPositionSizer(total_capital=250.0)

    # Test cases
    test_cases = [
        {'entry_price': 0.40, 'edge_pct': 25, 'confidence': 0.8, 'side': 'YES'},
        {'entry_price': 0.30, 'edge_pct': 50, 'confidence': 0.9, 'side': 'NO'},
        {'entry_price': 0.60, 'edge_pct': 10, 'confidence': 0.5, 'side': 'YES'},
        {'entry_price': 0.15, 'edge_pct': 50, 'confidence': 0.7, 'side': 'NO'},
    ]

    print("Kelly Position Sizing Examples:\n")
    for tc in test_cases:
        size = sizer.calculate_position_size(**tc)
        print(f"Entry: ${tc['entry_price']:.2f}, Edge: {tc['edge_pct']}%, Conf: {tc['confidence']:.0%}, Side: {tc['side']}")
        print(f"  -> Position: ${size.amount_usd:.2f}")
        print(f"  -> {size.reasoning}\n")
