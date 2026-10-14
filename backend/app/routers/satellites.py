"""API routes for satellites, positions and ground tracks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from .. import celestrak, db, orbit
from ..logging_conf import get_logger

log = get_logger("routers.satellites")

router = APIRouter(prefix="/api/satellites", tags=["satellites"])


def _parse_time(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid time '{raw}'. Use ISO-8601 UTC, e.g. 2024-10-06T12:00:00Z.",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/groups")
def list_groups() -> dict[str, Any]:
    """Cached group summary plus the groups that can be loaded from CelesTrak."""
    return {
        "cached": db.groups_summary(),
        "available": [{"id": k, "label": v} for k, v in celestrak.GROUPS.items()],
    }


@router.post("/refresh/{group}")
def refresh_group(group: str, force: bool = Query(False)) -> dict[str, Any]:
    """Ensure a group's TLE cache is fresh; `force=true` triggers a manual refetch."""
    try:
        return celestrak.fetch_group(group, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except celestrak.FetchError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("")
def list_satellites(
    group: Optional[str] = Query(None, max_length=40),
    q: Optional[str] = Query(None, max_length=100, description="Name or NORAD ID fragment"),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List cached satellites with optional group filter and search text."""
    if group and group not in celestrak.GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group '{group}'.")
    rows = db.list_satellites(group=group, search=q)
    total = len(rows)
    page = rows[offset: offset + limit]
    updated = max((r["fetched_at"] for r in rows), default=None)
    source = page[0]["source"] if page else None
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {"norad_id": r["norad_id"], "name": r["name"], "object_id": r["object_id"],
             "group": r["group_name"], "source": r["source"], "data_fetched_at": r["fetched_at"]}
            for r in page
        ],
        "data_source": source,
        "last_updated": updated,
    }


@router.get("/{norad_id}")
def get_satellite(norad_id: str) -> dict[str, Any]:
    """Retrieve one satellite with orbital elements parsed from its TLE."""
    if not norad_id.isdigit():
        raise HTTPException(status_code=422, detail="Satellite ID must be numeric (NORAD catalog ID).")
    sat = db.get_satellite(norad_id)
    if not sat:
        raise HTTPException(status_code=404, detail=f"No cached satellite with NORAD ID {norad_id}.")
    try:
        els = orbit.parse_tle_elements(sat["tle_line1"], sat["tle_line2"])
    except orbit.OrbitError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {**sat, "orbital_elements": els}


@router.get("/{norad_id}/position")
def satellite_position(
    norad_id: str,
    time: Optional[str] = Query(None, description="ISO-8601 UTC time; defaults to now."),
) -> dict[str, Any]:
    """SGP4-calculated position at a requested time (clearly marked as computed)."""
    when = _parse_time(time) if time else datetime.now(timezone.utc)
    sat = db.get_satellite(norad_id)
    if not sat:
        raise HTTPException(status_code=404, detail=f"No cached satellite with NORAD ID {norad_id}.")
    try:
        pos = orbit.compute_position(sat["tle_line1"], sat["tle_line2"], when)
    except orbit.OrbitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "satellite": {"norad_id": sat["norad_id"], "name": sat["name"],
                      "source": sat["source"], "data_fetched_at": sat["fetched_at"]},
        "kind": "calculated_from_tle",
        "note": "Position is calculated with SGP4 from cached TLE data — not a live observation.",
        "position": pos,
    }


@router.get("/{norad_id}/ground-track")
def satellite_ground_track(
    norad_id: str,
    time: Optional[str] = Query(None, description="ISO-8601 UTC center time; defaults to now."),
    points: int = Query(72, ge=2, le=360),
) -> dict[str, Any]:
    """Ground-track points for roughly one orbit centered on the requested time."""
    when = _parse_time(time) if time else datetime.now(timezone.utc)
    sat = db.get_satellite(norad_id)
    if not sat:
        raise HTTPException(status_code=404, detail=f"No cached satellite with NORAD ID {norad_id}.")
    try:
        track = orbit.compute_ground_track(sat["tle_line1"], sat["tle_line2"], when, points=points)
    except orbit.OrbitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "satellite": {"norad_id": sat["norad_id"], "name": sat["name"],
                      "source": sat["source"], "data_fetched_at": sat["fetched_at"]},
        "kind": "calculated_from_tle",
        "centered_at": when.isoformat(),
        "points": track,
    }
