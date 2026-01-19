"""VolumeHeatIndex strategy

Compute a simple 1m/5m volume heat ratio and return a SIGNAL_STRENGTH
value in the range 0-100.

Usage:
    vhi = VolumeHeatIndex()
    strength = vhi.score(volume_1m, volume_5m)

The implementation is intentionally lightweight so it can be used in tests
and as an easy-to-integrate strategy building block.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class VolumeHeatIndex:
    """Compute a heat index from 1-minute and 5-minute volumes.

    The score is proportional to the ratio r = (vol_1m / (vol_5m / 5))
    which compares the most recent minute to the per-minute average of the
    last 5 minutes. The resulting ratio is mapped to a 0-100 scale and
    clamped.
    """
    max_ratio: float = 5.0  # ratio mapped to 100 (tunable)

    def score(self, vol_1m: float, vol_5m: float) -> int:
        """Return SIGNAL_STRENGTH in 0..100.

        Args:
            vol_1m: volume in last 1 minute
            vol_5m: volume in last 5 minutes
        """
        try:
            vol_1m_f = float(vol_1m or 0.0)
            vol_5m_f = float(vol_5m or 0.0)
        except Exception:
            return 0

        # protect against division by zero
        if vol_5m_f <= 0.0:
            # If there's volume in 1m but none in 5m, treat as max heat
            return 100 if vol_1m_f > 0 else 0

        per_min_avg = vol_5m_f / 5.0
        if per_min_avg <= 0:
            return 100 if vol_1m_f > 0 else 0

        ratio = vol_1m_f / per_min_avg
        # normalize ratio to [0, max_ratio]
        norm = max(0.0, min(ratio, self.max_ratio))
        # map to 0..100
        strength = int(round((norm / self.max_ratio) * 100.0))
        return max(0, min(100, strength))


if __name__ == '__main__':
    # tiny smoke test
    v = VolumeHeatIndex()
    print(v.score(100, 200))  # 100 in 1m vs 200 in 5m -> per-min=40 -> ratio=2.5 -> ~50
