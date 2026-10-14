"""External data fetching from CelesTrak, with SQLite-backed caching.

CelesTrak usage requirements (https://celestrak.org/):
- Do not query more than once every 2 hours per dataset. We enforce a
  configurable cache TTL (default 15 min for MVP usability is *more* frequent
  than required only when the user manually forces a refresh; automatic
  refreshes respect ORBITDESK_CACHE_TTL_SECONDS and we throttle manual
  refreshes to one per minute).
- TLE data is provided for civilian space enthusiasts; not for operational use.
  The UI displays this disclaimer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from . import db
from .config import settings
from .logging_conf import get_logger
from .tle import DEMO_TLES, parse_tle_file

log = get_logger("celestrak")

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

# Allowed groups -> display labels. Restricting to a whitelist keeps URL safe.
GROUPS: dict[str, str] = {
    "stations": "Space Stations",
    "starlink": "Starlink",
    "gps-ops": "GPS Operational",
    "glo-ops": "GLONASS Operational",
    "galileo": "Galileo",
    "oneweb": "OneWeb",
    "weather": "Weather",
    "resource": "Earth Resources",
    "communications": "Communications",
    "science": "Science",
}

_LAST_MANUAL_REFRESH_KEY = "last_refresh_epoch"
_MIN_MANUAL_REFRESH_SECONDS = 60


class FetchError(RuntimeError):
    """Raised when live data cannot be fetched."""


def fetch_group(group: str, force: bool = False) -> dict[str, Any]:
    """Ensure the cache holds fresh TLEs for `group`.

    Returns metadata: {group, count, source, last_updated, mode, notice}.
    Falls back to demo data if the network/CelesTrak fails and no live cache
    exists yet.
    """
    if group not in GROUPS:
        raise ValueError(f"Unknown satellite group '{group}'")

    now = datetime.now(timezone.utc)
    existing = db.list_satellites(group=group)
    last_updated_iso = db.get_state(f"updated:{group}")

    def _fresh(iso: str | None) -> bool:
        if not iso:
            return False
        try:
            ts = datetime.fromisoformat(iso)
        except ValueError:
            return False
        age = (now - ts).total_seconds()
        return age < settings.cache_ttl_seconds

    # Decide whether to hit the network.
    if settings.force_demo_mode:
        if not existing or any(s["source"] != "demo" for s in existing):
            n = db.replace_group(group, parse_tle_file(DEMO_TLES), source="demo", fetched_at=now)
            db.set_state(f"updated:{group}", now.isoformat())
            log.info("Demo mode active; loaded %d demo satellites for %s", n, group)
        return _meta(group, "demo", now, notice="Demo mode is enabled (ORBITDESK_FORCE_DEMO_MODE).")

    if force:
        last = db.get_state(_LAST_MANUAL_REFRESH_KEY)
        if last:
            try:
                elapsed = (now.timestamp() - float(last))
            except ValueError:
                elapsed = _MIN_MANUAL_REFRESH_SECONDS + 1
            if elapsed < _MIN_MANUAL_REFRESH_SECONDS:
                wait = int(_MIN_MANUAL_REFRESH_SECONDS - elapsed)
                raise FetchError(
                    f"Manual refresh too soon; CelesTrak rate limits apply. Try again in {wait}s."
                )
        db.set_state(_LAST_MANUAL_REFRESH_KEY, str(now.timestamp()))
    elif existing and _fresh(last_updated_iso):
        return _meta(group, existing[0]["source"], datetime.fromisoformat(last_updated_iso))

    # Fetch from CelesTrak.
    try:
        url = f"{BASE_URL}?GROUP={group}&FORMAT=tle"
        with httpx.Client(timeout=settings.fetch_timeout_seconds, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "OrbitDesk/1.0 (personal dashboard)"})
            resp.raise_for_status()
        records = parse_tle_file(resp.text)
        if not records:
            raise FetchError("CelesTrak returned no parseable TLE data.")
        db.replace_group(group, records, source="celestrak", fetched_at=now)
        db.set_state(f"updated:{group}", now.isoformat())
        log.info("Fetched %d satellites for group '%s' from CelesTrak", len(records), group)
        return _meta(group, "celestrak", now)
    except (httpx.HTTPError, FetchError) as exc:
        log.warning("CelesTrak fetch failed for '%s': %s", group, exc)
        if existing:
            return _meta(
                group,
                existing[0]["source"],
                datetime.fromisoformat(last_updated_iso) if last_updated_iso else now,
                notice=f"Live fetch failed ({exc.__class__.__name__}); serving cached data.",
            )
        # No cache at all: fall back to clearly-labeled demo data.
        records = parse_tle_file(DEMO_TLES)
        db.replace_group(group, records, source="demo", fetched_at=now)
        db.set_state(f"updated:{group}", now.isoformat())
        return _meta(
            group,
            "demo",
            now,
            notice=(
                "Could not reach CelesTrak, so DEMO data is shown instead. "
                "Positions are approximate. Check your connection and refresh."
            ),
        )


def _meta(group: str, source: str, updated: datetime, notice: str | None = None) -> dict[str, Any]:
    rows = db.list_satellites(group=group)
    return {
        "group": group,
        "label": GROUPS.get(group, group),
        "count": len(rows),
        "source": source,
        "last_updated": updated.astimezone(timezone.utc).isoformat(),
        "notice": notice,
    }
