from __future__ import annotations

import re
from typing import Optional


def parse_compact_number(value: object) -> Optional[int]:
    """Parse Instagram's localized 12K / 1,2 M counter without guessing absent values."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip().upper().replace("\u00a0", " ")
    if not raw:
        return None
    raw = raw.replace(" ", "")
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)([KM]?)", raw)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    multiplier = {"K": 1_000, "M": 1_000_000, "": 1}[match.group(2)]
    return int(amount * multiplier)


def viral_ratio(views: Optional[int], followers: Optional[int]) -> Optional[float]:
    if views is None or followers is None or followers <= 0:
        return None
    return views / followers


def viral_label(ratio: Optional[float]) -> str:
    if ratio is None:
        return "insufficient_metrics"
    if ratio < 4:
        return "reject"
    if ratio < 5:
        return "viral"
    if ratio <= 10:
        return "strong_viral"
    return "exceptional"
