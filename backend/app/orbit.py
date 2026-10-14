"""Orbit propagation using Skyfield (SGP4 from TLE data).

Kept separate from data fetching and HTTP concerns. All public functions take
plain dicts/strings so they are easy to test with demo TLEs.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from skyfield.api import EarthSatellite
from skyfield.positionlib import Geocentric

from .logging_conf import get_logger

log = get_logger("orbit")

_ts = None  # skyfield timescale, lazily created
_lock = threading.Lock()


class OrbitError(RuntimeError):
    """Raised when a TLE cannot be parsed or a position cannot be computed."""


def _timescale():
    """Lazily build the Skyfield timescale (downloads leap seconds once).

    Falls back to a fixed-offset scale if the download fails so offline demo
    mode still computes positions.
    """
    global _ts
    with _lock:
        if _ts is not None:
            return _ts
        try:
            from skyfield.api import load
            _ts = load.timescale(builtin=True)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to error
            log.error("Could not init Skyfield timescale: %s", exc)
            raise OrbitError("Timescale unavailable; cannot compute positions.") from exc
        return _ts


def make_satellite(tle_line1: str, tle_line2: str) -> EarthSatellite:
    l1 = (tle_line1 or "").strip()
    l2 = (tle_line2 or "").strip()
    if not l1.startswith("1 ") or not l2.startswith("2 "):
        raise OrbitError("Malformed TLE: lines must start with '1 ' and '2 '.")
    try:
        return EarthSatellite(l1, l2, name="", ts=_timescale())
    except ValueError as exc:
        raise OrbitError(f"Malformed TLE data: {exc}") from exc


def tle_epoch(sat: EarthSatellite) -> datetime:
    """Epoch of the TLE elements as a UTC datetime."""
    return sat.epoch.utc_datetime().replace(tzinfo=timezone.utc)



def parse_tle_elements(tle_line1: str, tle_line2: str) -> dict[str, float]:
    """Parse raw orbital elements directly from the TLE text.

    Skyfield's Satrec stores angles in radians and mean motion in rad/min, so
    for display we read the decimal values straight from the TLE columns.
    Raises OrbitError if the lines are too short / unparseable.
    """
    l1 = (tle_line1 or "").rstrip("\n")
    l2 = (tle_line2 or "").rstrip("\n")
    # Column layout per the TLE standard (1-based, inclusive) for line 2:
    #   inclination 9-16, RAAN 18-25, eccentricity 27-33,
    #   arg. of perigee 35-42, mean anomaly 44-51, mean motion 53-63.
    # Zero-based Python slices used below: [8:16], [17:25], [26:33],
    #   [34:42], [43:51], [52:63].
    # (Epoch year/day come from line 1 columns 19-20 and 21-32 -> [18:20], [20:32].)
    try:
        epoch_year = int(l1[18:20])
        epoch_day = float(l1[20:32])
        inclination = float(l2[8:16])
        raan = float(l2[17:25])
        eccentricity = float("0." + l2[26:33])
        arg_perigee = float(l2[34:42])
        mean_anomaly = float(l2[43:51])
        mean_motion = float(l2[52:63])
    except (ValueError, IndexError) as exc:
        raise OrbitError(f"Could not parse orbital elements from TLE: {exc}") from exc
    year = 2000 + epoch_year if epoch_year < 57 else 1900 + epoch_year
    return {
        "epoch_year": year,
        "epoch_day_of_year": epoch_day,
        "inclination_deg": inclination,
        "raan_deg": raan,
        "eccentricity": eccentricity,
        "arg_perigee_deg": arg_perigee,
        "mean_anomaly_deg": mean_anomaly,
        "mean_motion_revs_day": mean_motion,
    }


def compute_ground_track(
    tle_line1: str,
    tle_line2: str,
    time_utc: datetime,
    points: int = 72,
    span_minutes: Optional[float] = None,
) -> list[dict[str, float]]:
    """Compute an orbital ground track centered on `time_utc`.

    Returns a list of {lat, lon, time} samples. `span_minutes` defaults to one
    full orbital period estimated from mean motion.
    """
    sat = make_satellite(tle_line1, tle_line2)
    ts = _timescale()

    period_min = 1440.0 / parse_tle_elements(tle_line1, tle_line2)["mean_motion_revs_day"]
    if span_minutes is None:
        span_minutes = min(period_min, 110.0)
    points = max(2, min(int(points), 500))

    start = time_utc.timestamp() - (span_minutes * 60.0) / 2.0
    out: list[dict[str, float]] = []
    for i in range(points + 1):
        t_epoch = datetime.fromtimestamp(start + (span_minutes * 60.0) * i / points, tz=timezone.utc)
        t = ts.from_datetime(t_epoch)
        g = sat.at(t)
        sublunar = g.subpoint()
        out.append(
            {
                "lat": round(float(sublunar.latitude.degrees), 4),
                "lon": round(float(sublunar.longitude.degrees), 4),
                "time": t_epoch.isoformat(),
            }
        )
    return out


def compute_position(tle_line1: str, tle_line2: str, time_utc: datetime) -> dict[str, Any]:
    """Compute geodetic sub-satellite point, altitude and velocity at a time."""
    sat = make_satellite(tle_line1, tle_line2)
    ts = _timescale()
    t = ts.from_datetime(time_utc)
    geo: Geocentric = sat.at(t)
    sublunar = geo.subpoint()
    lat = float(sublunar.latitude.degrees)
    lon = float(sublunar.longitude.degrees)
    alt_km = float(sublunar.elevation.km)
    import numpy as np
    speed_km_s = float(np.linalg.norm(geo.velocity.km_per_s))

    # Orbital elements read directly from the TLE text (degrees / revs per day).
    el = parse_tle_elements(tle_line1, tle_line2)
    no_kozai = el["mean_motion_revs_day"]
    ecc = el["eccentricity"]
    period_min = 1440.0 / no_kozai if no_kozai else None
    apogee_km = None
    perigee_km = None
    if period_min:
        # Kepler's third law: a = (GM * (T / 2*pi)^2)^(1/3)
        import math
        gm = 398600.4418
        T = period_min * 60.0
        a = (gm * (T / (2 * math.pi)) ** 2) ** (1.0 / 3.0)
        apogee_km = a * (1 + ecc) - 6378.137
        perigee_km = a * (1 - ecc) - 6378.137

    return {
        "computed_at": time_utc.astimezone(timezone.utc).isoformat(),
        "tle_epoch": tle_epoch(sat).isoformat(),
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "altitude_km": round(alt_km, 2),
        "velocity_km_s": round(speed_km_s, 3),
        "inclination_deg": el["inclination_deg"],
        "raan_deg": el["raan_deg"],
        "arg_perigee_deg": el["arg_perigee_deg"],
        "eccentricity": ecc,
        "mean_motion_revs_day": no_kozai,
        "period_minutes": round(period_min, 2) if period_min else None,
        "apogee_km": round(apogee_km, 1) if apogee_km is not None else None,
        "perigee_km": round(perigee_km, 1) if perigee_km is not None else None,
    }
