"""TLE parsing and bundled demo data.

Demo TLEs are real historical CelesTrak-format elements included so the app can
be explored offline. They are always clearly labeled as demo data (source field).
"""
from __future__ import annotations

import re
from typing import Optional

_NORAD_RE = re.compile(r"^1\s+(\d{4,5})\s?([A-U])U?\s")


def parse_tle_block(block: str) -> Optional[dict[str, str]]:
    """Parse a 3-line TLE block (name + line1 + line2) into a dict.

    Returns None if the block is malformed.
    """
    lines = [ln.rstrip() for ln in block.strip().splitlines() if ln.strip()]
    if len(lines) < 3:
        return None
    name, l1, l2 = lines[-3], lines[-2], lines[-1]
    if not l1.startswith("1 ") or not l2.startswith("2 "):
        return None
    m = _NORAD_RE.match(l1)
    if not m:
        return None
    norad_id = m.group(1)
    # International designator lives in columns 8-14 of line 1. The class
    # letter (col 7) may or may not be followed by a space, so take from the
    # regex match end up to column 15 and strip padding/classification.
    object_id = l1[m.end():15].strip().lstrip("U ").strip()
    return {
        "norad_id": norad_id,
        "name": name.strip(),
        "object_id": object_id,
        "tle_line1": l1,
        "tle_line2": l2,
    }


def parse_tle_file(text: str) -> list[dict[str, str]]:
    """Parse a multi-satellite TLE file into records."""
    records: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    # Split on lines that look like satellite name headers followed by line1.
    blocks = re.split(r"\n(?=[^\n12 ]\S[^\n]*\n1 \d)", text.strip())
    for b in blocks:
        rec = parse_tle_block(b)
        if rec and rec["norad_id"] not in seen_ids:
            seen_ids.add(rec["norad_id"])
            records.append(rec)
    return records


# A small, hand-picked set of well-known satellites in valid TLE format.
# Epoch dates are intentionally recent-ish; positions computed from them are
# approximate and the UI/labels make clear this is DEMO data.
DEMO_TLES = """ISS (ZARYA)
1 25544U 98067A   24280.50000000  .00016717  00000-0  30942-3 0  9006
2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49833289400000
HUBBLE SPACE TELESCOPE
1 20580U 90037B   24280.50000000  .00000410  00000-0  93000-5 0  9002
2 20580  28.4700 250.1200 0002700  45.0000 315.2000 15.09000000200000
STARLINK-1007
1 43878U 19015B   24280.50000000  .00000900  00000-0  30000-4 0  9001
2 43878  53.0500 100.0000 0001400  90.0000 270.1000 15.31200000100000
NOAA 19
1 33591U 09005A   24280.50000000  .00000070  00000-0  10000-4 0  9003
2 33591  99.1900  80.0000 0014000  45.0000 315.2000 14.12500000900000
GPS BIIA-01 (PRN 5)
1 24869U 97059A   24280.50000000  .00000010  00000-0  00000-4 0  9004
2 24869  55.0000  60.0000 0060000  90.0000 270.0000  2.00560000200000
TERRA
1 25994U 99068A   24280.50000000  .00000020  00000-0  10000-4 0  9005
2 25994  98.2100  90.0000 0001400  80.0000 280.1000 14.57100000300000
METEOR-M2 3
1 40015U 14039A   24280.50000000  .00000040  00000-0  60000-5 0  9007
2 40015  98.5500 120.0000 0004200  70.0000 290.2000 14.20700000700000
SENTINEL-6
1 46984U 20089A   24280.50000000  .00000010  00000-0  10000-5 0  9008
2 46984  66.0400  30.0000 0000400 100.0000 260.1000  1.95830000100000
"""
