"""Historical traffic baseline (empty template).

Populate with cumulative GiB per user-email if you have past data from
Telegram bot reports (or any other source) that you want to add to the
dashboard. Values are split into uplink/downlink using the global ratio.
"""
from typing import Dict

_GIB = 1024 ** 3
_UPLINK_RATIO = 0.05
_DOWNLINK_RATIO = 1.0 - _UPLINK_RATIO

_TOTAL_GIB: Dict[str, float] = {
    # "user@example": 12.34,
}

BASELINE: Dict[str, Dict[str, int]] = {
    email: {
        "uplink": int(gib * _GIB * _UPLINK_RATIO),
        "downlink": int(gib * _GIB * _DOWNLINK_RATIO),
    }
    for email, gib in _TOTAL_GIB.items()
}
