"""Tests for raw TLE parsing — guards against wrong column offsets.

Regression note: a previous version used l2[34:44] for inclination, which read
the argument-of-perigee field plus trailing digits and crashed on valid TLEs.
The correct zero-based slices are documented in orbit.parse_tle_elements.
"""
from __future__ import annotations

import math

import pytest
from skyfield.api import EarthSatellite, load

from backend.app.orbit import OrbitError, parse_tle_elements

# Known-good ISS TLE (CelesTrak format). Expected values verified by hand
# against the standard column positions AND cross-checked vs Skyfield below.
ISS_L1 = "1 25544U 98067A   24280.50000000  .00016717  00000-0  30942-3 0  9006"
ISS_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49833289400000"


def test_iss_elements_match_standard_columns():
    els = parse_tle_elements(ISS_L1, ISS_L2)
    assert els["inclination_deg"] == pytest.approx(51.6416, abs=1e-4)
    assert els["raan_deg"] == pytest.approx(247.4627, abs=1e-4)
    assert els["eccentricity"] == pytest.approx(0.0006703, abs=1e-7)
    assert els["arg_perigee_deg"] == pytest.approx(130.5360, abs=1e-4)
    assert els["mean_anomaly_deg"] == pytest.approx(325.0288, abs=1e-4)
    assert els["mean_motion_revs_day"] == pytest.approx(15.49833289, abs=1e-8)
    assert els["epoch_year"] == 2024
    assert els["epoch_day_of_year"] == pytest.approx(280.5, abs=1e-6)


def test_elements_agree_with_skyfield():
    """Every parsed line-2 field must match Skyfield's own SGP4 propagation."""
    els = parse_tle_elements(ISS_L1, ISS_L2)
    ts = load.timescale(builtin=True)
    rec = EarthSatellite(ISS_L1, ISS_L2, ts=ts).model  # sgp4 Satrec at epoch
    # Satrec stores angles in radians and mean motion in revs per minute.
    assert els["inclination_deg"] == pytest.approx(math.degrees(rec.inclo), abs=1e-3)
    assert els["raan_deg"] == pytest.approx(math.degrees(rec.nodeo), abs=1e-3)
    assert els["arg_perigee_deg"] == pytest.approx(math.degrees(rec.argpo), abs=1e-3)
    assert els["mean_anomaly_deg"] == pytest.approx(math.degrees(rec.mo), abs=1e-2)
    assert els["eccentricity"] == pytest.approx(rec.ecco, abs=1e-6)
    assert els["mean_motion_revs_day"] == pytest.approx(rec.no_kozai * 1440 / (2 * math.pi), abs=1e-4)


def test_leading_blank_inclination_field():
    """Inclination < 10 deg is right-justified with a leading space."""
    l2 = "2 90000   8.2500  12.3456 0012340  98.7654  65.4321  1.23456789123451"
    els = parse_tle_elements("1 90000U 00001A   24280.50000000  .00000000  00000-0  00000-0 0  9001", l2)
    assert els["inclination_deg"] == pytest.approx(8.25, abs=1e-4)
    assert els["raan_deg"] == pytest.approx(12.3456, abs=1e-4)
    assert els["eccentricity"] == pytest.approx(0.001234, abs=1e-7)
    assert els["arg_perigee_deg"] == pytest.approx(98.7654, abs=1e-4)
    assert els["mean_anomaly_deg"] == pytest.approx(65.4321, abs=1e-4)
    assert els["mean_motion_revs_day"] == pytest.approx(1.23456789, abs=1e-8)


def test_malformed_tle_raises():
    with pytest.raises(OrbitError):
        parse_tle_elements("1 short", "2 also short")
