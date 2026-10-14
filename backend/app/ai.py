"""AI assistant grounded in backend data.

The assistant answers questions about satellites shown in OrbitDesk. It never
invents orbital facts: every factual claim must come from our own tools
(satellite search, TLE details, SGP4 position/ground-track calculations).

Flow:
  1. Send the user's question to an OpenAI-compatible chat-completions API
     with JSON tool schemas (server-side key only; never sent to the browser).
  2. If the model requests a tool, execute it against our modules and feed the
     result back (up to MAX_TOOL_ROUNDS).
  3. Return the model's final answer together with the list of tool calls made,
     so the UI can show exactly which data grounded the response.

If OPENAI_API_KEY is not configured, `chat()` returns a clear disabled message
and the rest of the app keeps working.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import celestrak, db, orbit
from .config import settings
from .logging_conf import get_logger

log = get_logger("ai")

MAX_TOOL_ROUNDS = 4
REQUEST_TIMEOUT_SECONDS = 60.0

DISABLED_MESSAGE = (
    "The AI assistant is not enabled because no API key is configured on the "
    "server. To enable it, set the OPENAI_API_KEY environment variable (see "
    ".env.example) and restart the backend. Everything else in OrbitDesk — "
    "the satellite list, map, positions and ground tracks — works without it."
)

SYSTEM_PROMPT = (
    "You are OrbitDesk's mission-control assistant. You answer questions about "
    "Earth satellites using ONLY the provided tools, which read cached CelesTrak "
    "TLE data and compute SGP4 positions via Skyfield.\n"
    "Rules:\n"
    "- Never invent or estimate live positions, orbital elements, predictions or "
    "satellite details. If a tool does not return the needed data, say so plainly.\n"
    "- Call tools before answering any factual question about a satellite.\n"
    "- Mention the satellite name and the relevant timestamps (data fetched_at / "
    "TLE epoch / computed_at) when reporting numbers.\n"
    "- Positions you report are CALCULATED from TLE data at a given time, not "
    "live observations; say so when relevant.\n"
    "- Be concise. Use plain text with short bullet lists where helpful."
)


# --------------------------- tools (backend functions) ---------------------------

def _tool_search_satellites(name: str, group: Optional[str] = None, limit: int = 10) -> Any:
    rows = db.list_satellites(group=group or None, search=name or None)
    return {
        "count": len(rows),
        "results": [
            {"norad_id": r["norad_id"], "name": r["name"], "group": r["group_name"],
             "data_fetched_at": r["fetched_at"], "source": r["source"]}
            for r in rows[: max(1, min(int(limit), 50))]
        ],
    }


def _tool_satellite_details(norad_id: str) -> Any:
    sat = db.get_satellite(norad_id)
    if not sat:
        return {"error": f"No satellite with NORAD ID {norad_id} in the cache. "
                         "It may not be part of a loaded group."}
    try:
        els = orbit.parse_tle_elements(sat["tle_line1"], sat["tle_line2"])
    except orbit.OrbitError as exc:
        return {"error": str(exc)}
    return {
        "norad_id": sat["norad_id"],
        "name": sat["name"],
        "object_id": sat["object_id"],
        "group": sat["group_name"],
        "source": sat["source"],
        "data_fetched_at": sat["fetched_at"],
        "orbital_elements": els,
        "tle_line1": sat["tle_line1"],
        "tle_line2": sat["tle_line2"],
    }


def _tool_satellite_position(norad_id: str, time_utc: Optional[str] = None) -> Any:
    sat = db.get_satellite(norad_id)
    if not sat:
        return {"error": f"No satellite with NORAD ID {norad_id} in the cache."}
    if time_utc:
        try:
            when = datetime.fromisoformat(time_utc.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            return {"error": f"Invalid ISO-8601 time '{time_utc}'."}
    else:
        when = datetime.now(timezone.utc)
    try:
        pos = orbit.compute_position(sat["tle_line1"], sat["tle_line2"], when)
    except orbit.OrbitError as exc:
        return {"error": str(exc)}
    return {"name": sat["name"], "source": sat["source"],
            "data_fetched_at": sat["fetched_at"], "position": pos}


def _tool_satellite_ground_track(norad_id: str, points: int = 72) -> Any:
    sat = db.get_satellite(norad_id)
    if not sat:
        return {"error": f"No satellite with NORAD ID {norad_id} in the cache."}
    try:
        track = orbit.compute_ground_track(
            sat["tle_line1"], sat["tle_line2"], datetime.now(timezone.utc),
            points=max(2, min(int(points), 200)))
    except orbit.OrbitError as exc:
        return {"error": str(exc)}
    return {"name": sat["name"], "points": track,
            "note": "Calculated sub-satellite track around now; not a live observation."}


def _tool_list_groups() -> Any:
    return {"groups": db.groups_summary(),
            "available_for_refresh": sorted(celestrak.GROUPS.keys())}


TOOL_FUNCS: dict[str, Any] = {
    "search_satellites": _tool_search_satellites,
    "satellite_details": _tool_satellite_details,
    "satellite_position": _tool_satellite_position,
    "satellite_ground_track": _tool_satellite_ground_track,
    "list_groups": _tool_list_groups,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_satellites",
        "description": "Search cached satellites by name fragment or NORAD ID.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Name fragment or NORAD ID"},
            "group": {"type": "string", "description": "Optional group filter"},
            "limit": {"type": "integer", "description": "Max results (default 10)"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "satellite_details",
        "description": "Full cached details + orbital elements for one NORAD ID.",
        "parameters": {"type": "object", "properties": {
            "norad_id": {"type": "string"}}, "required": ["norad_id"]}}},
    {"type": "function", "function": {
        "name": "satellite_position",
        "description": "SGP4-calculated latitude/longitude/altitude/velocity at an "
                       "ISO-8601 UTC time (defaults to now). Calculated, not observed.",
        "parameters": {"type": "object", "properties": {
            "norad_id": {"type": "string"},
            "time_utc": {"type": "string", "description": "ISO-8601 UTC, optional"}},
            "required": ["norad_id"]}}},
    {"type": "function", "function": {
        "name": "satellite_ground_track",
        "description": "Ground-track points for one orbit around now.",
        "parameters": {"type": "object", "properties": {
            "norad_id": {"type": "string"},
            "points": {"type": "integer"}}, "required": ["norad_id"]}}},
    {"type": "function", "function": {
        "name": "list_groups",
        "description": "Cached satellite groups with counts and last-update times.",
        "parameters": {"type": "object", "properties": {}}}},
]


def _execute_tool(name: str, args: dict[str, Any]) -> str:
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool '{name}'."})
    try:
        result = fn(**args)
    except TypeError as exc:
        return json.dumps({"error": f"Bad tool arguments: {exc}"})
    except Exception as exc:  # noqa: BLE001 - tools must never crash the chat
        log.exception("Tool %s failed", name)
        return json.dumps({"error": f"Tool error: {exc}"})
    return json.dumps(result, default=str)


# --------------------------- chat entry point ---------------------------

def ai_status() -> dict[str, Any]:
    return {"enabled": settings.ai_enabled, "model": settings.openai_model if settings.ai_enabled else None}


def chat(question: str, history: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
    """Answer `question` using backend tools. Returns {answer, tool_calls, grounded}."""
    if not settings.ai_enabled:
        return {"answer": DISABLED_MESSAGE, "tool_calls": [], "grounded": False, "disabled": True}

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        if h.get("role") in {"user", "assistant"} and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})

    tool_log: list[dict[str, Any]] = []
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                resp = client.post(url, headers=headers, json={
                    "model": settings.openai_model,
                    "messages": messages,
                    "tools": TOOL_SCHEMAS,
                })
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    return {"answer": (msg.get("content") or "").strip()
                            or "The model returned no answer.",
                            "tool_calls": tool_log, "grounded": bool(tool_log), "disabled": False}
                messages.append(msg)
                for c in calls:
                    fn = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = _execute_tool(fn, args)
                    tool_log.append({"tool": fn, "args": args})
                    messages.append({"role": "tool", "tool_call_id": c.get("id", fn),
                                     "name": fn, "content": result})
            return {"answer": "I hit the tool-call limit while researching that question. "
                              "Please narrow it down and ask again.",
                    "tool_calls": tool_log, "grounded": bool(tool_log), "disabled": False}
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = ("The AI provider rejected the request (check OPENAI_API_KEY / OPENAI_MODEL)."
                if code == 401 else "The AI provider returned an error.")
        log.warning("AI request failed: HTTP %s", code)
        return {"answer": f"Sorry, I couldn't reach the AI service. {hint}",
                "tool_calls": tool_log, "grounded": False, "disabled": False, "error": True}
    except httpx.HTTPError as exc:
        log.warning("AI request failed: %s", exc)
        return {"answer": "Sorry, I couldn't reach the AI service (network problem). "
                          "Non-AI features still work normally.",
                "tool_calls": tool_log, "grounded": False, "disabled": False, "error": True}
