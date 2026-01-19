"""GraduationSniper listener for Pump.fun migration events

This module provides a small, testable listener that inspects a
pump/migration event and decides whether the characteristics are suitable
for a "graduation sniper" style alert.

It checks two simple conditions:
 - is_lp_burned: boolean indicator that liquidity pair was burned
 - top_holder_concentration: fraction (0..1) representing fraction of
   token supply held by top holder(s). Lower is better for sniping.

The listener provides a `handle_migration_event(event: dict)` method that
returns a tuple `(action: bool, confidence: float, details: dict)`.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class MigrationEvent:
    mint: str
    is_lp_burned: bool
    top_holder_concentration: float  # 0-1 (0 = fully distributed, 1 = single wallet)
    extra: Optional[dict] = None


class GraduationSniper:
    """Simple logic to decide whether to generate an actionable signal
    when a token is observed to migrate to Pump.fun (or similar) during
    a listing/migration event.
    """

    def __init__(self, max_top_holder_concentration: float = 0.2):
        # top holder concentration threshold (0..1);
        # below this value -> more distributed -> better for sniping
        self.max_top_holder_concentration = float(max_top_holder_concentration)

    async def handle_migration_event(self, event: dict):
        """Inspect an event dict and return (action, confidence, details).

        Expected event shape (example):
          {
            'mint': 'TOKEN...'
            'is_lp_burned': True/False,
            'top_holder_concentration': 0.12,
            ...
          }
        """
        try:
            m = MigrationEvent(
                mint=event.get('mint') or event.get('token') or '',
                is_lp_burned=bool(event.get('is_lp_burned', False)),
                top_holder_concentration=float(event.get('top_holder_concentration', 1.0)),
                extra=event.get('extra') if isinstance(event.get('extra'), dict) else None,
            )
        except Exception:
            return False, 0.0, {'reason': 'malformed_event'}

        # Basic heuristics: require LP burn and reasonably low top-holder concentration
        if not m.is_lp_burned:
            return False, 0.0, {'reason': 'lp_not_burned'}

        # Confidence scales inversely with concentration (lower -> higher confidence)
        concentration = max(0.0, min(1.0, m.top_holder_concentration or 1.0))
        # If concentration is below threshold, produce an action with confidence
        if concentration <= self.max_top_holder_concentration:
            # map concentration 0..max_threshold -> confidence 100..(50)
            # lower concentration -> higher confidence
            if self.max_top_holder_concentration <= 0:
                confidence = 100.0
            else:
                frac = 1.0 - (concentration / self.max_top_holder_concentration)
                confidence = max(0.0, min(100.0, 50.0 + frac * 50.0))
            return True, confidence, {'mint': m.mint, 'concentration': concentration}

        # otherwise, not favorable
        return False, max(0.0, 50.0 - (concentration - self.max_top_holder_concentration) * 100.0), {'mint': m.mint, 'concentration': concentration}


# optional helper used by external event buses
def register_with_event_bus(event_bus, listener: GraduationSniper):
    """Register a simple handler if the event bus exposes register().

    This is intentionally defensive; many event bus implementations differ.
    """
    try:
        if hasattr(event_bus, 'register'):
            # register under a canonical name used by Pump.fun migration events
            event_bus.register('pump_migration', listener.handle_migration_event)
            return True
    except Exception:
        pass
    return False


if __name__ == '__main__':
    import asyncio

    async def demo():
        s = GraduationSniper(max_top_holder_concentration=0.25)
        print(await s.handle_migration_event({'mint': 'ABC', 'is_lp_burned': True, 'top_holder_concentration': 0.1}))

    asyncio.run(demo())
