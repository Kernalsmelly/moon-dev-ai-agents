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
from datetime import datetime, timezone
import src.config as config


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
        # Session-aware dynamic thresholding: compute an effective maximum
        # ratio that makes it proportionally harder/easier to reach full
        # strength depending on the active market session. We expose a
        # get_dynamic_threshold() helper below which returns a target score
        # value (0-100) for the current UTC hour; translate that into an
        # adjustment factor relative to the default baseline (55).
        try:
            effective_max = float(self.max_ratio)
            if getattr(config, 'DYNAMIC_THRESHOLD', False):
                # baseline score mapping uses 55 as the reference threshold
                baseline = 55.0
                dyn = float(self.get_dynamic_threshold())
                # larger dyn -> harder to reach 100, so shrink effective_max
                factor = baseline / dyn if dyn > 0 else 1.0
                effective_max = float(self.max_ratio) * float(factor)
        except Exception:
            effective_max = float(self.max_ratio)

        # normalize ratio to [0, effective_max]
        norm = max(0.0, min(ratio, effective_max))
        # map to 0..100 (scale by original max_ratio so higher effective_max
        # reduces the mapped strength, making it harder during high-noise sessions)
        try:
            strength = int(round((norm / float(effective_max)) * 100.0))
        except Exception:
            strength = 0
        return max(0, min(100, strength))

    def get_dynamic_threshold(self) -> int:
        """Return a session-aware score threshold (0-100) based on UTC hour.

        Rules:
            - NY Open (13:00 - 16:00 UTC): 70
            - Asia (00:00 - 08:00 UTC): 45
            - All other times: 55
        """
        try:
            now_h = datetime.now(timezone.utc).hour
            # NY open roughly 13:00-16:00 UTC
            if 13 <= now_h < 16:
                return 70
            # Asia session roughly midnight - 08:00 UTC
            if 0 <= now_h < 8:
                return 45
            return 55
        except Exception:
            return 55


if __name__ == '__main__':
    # tiny smoke test
    v = VolumeHeatIndex()
    print(v.score(100, 200))  # 100 in 1m vs 200 in 5m -> per-min=40 -> ratio=2.5 -> ~50
