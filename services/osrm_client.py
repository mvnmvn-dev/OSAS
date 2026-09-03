"""OSRM HTTP client for Table/Route/Trip with local heuristic fallback."""
from __future__ import annotations

import math
from typing import Any

import requests
from flask import current_app


def _base_url():
    return current_app.config.get("OSRM_BASE_URL", "http://127.0.0.1:5001").rstrip("/")


def _profile():
    return current_app.config.get("OSRM_PROFILE", "foot")


def _timeout():
    return float(current_app.config.get("OSRM_TIMEOUT_SECONDS", 8))


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _coords_param(points):
    # OSRM expects lon,lat
    return ";".join(f"{p['lng']},{p['lat']}" for p in points)


def is_available():
    try:
        resp = requests.get(f"{_base_url()}/health", timeout=min(2.0, _timeout()))
        return resp.status_code < 500
    except Exception:
        try:
            # Some builds expose /route only
            resp = requests.get(f"{_base_url()}/route/v1/{_profile()}/0,0;0.001,0.001", timeout=min(2.0, _timeout()))
            return resp.status_code in (200, 400)
        except Exception:
            return False


def trip(points: list[dict[str, Any]], roundtrip=True) -> dict:
    """
    Request an optimized visit order via OSRM Trip.
    points: [{lat, lng, id?, code?, name?, score?}, ...] with valid coordinates.
    Returns normalized dict with geometry, distance_m, duration_s, waypoint_order, engine.
    """
    if len(points) < 2:
        raise ValueError("At least two coordinates are required for a trip.")

    url = f"{_base_url()}/trip/v1/{_profile()}/{_coords_param(points)}"
    params = {
        "source": "first",
        "destination": "last" if not roundtrip else "any",
        "roundtrip": "true" if roundtrip else "false",
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=_timeout())
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            raise RuntimeError(data.get("message") or data.get("code") or "OSRM trip failed")
        trip_obj = data["trips"][0]
        order = [w.get("waypoint_index", i) for i, w in enumerate(data.get("waypoints") or [])]
        # OSRM waypoints are returned in input order; waypoint_index is position in trip
        ordered_idx = sorted(range(len(points)), key=lambda i: (data["waypoints"][i].get("waypoint_index", i)))
        return {
            "engine": "OSRM",
            "distance_m": int(round(trip_obj.get("distance") or 0)),
            "duration_s": int(round(trip_obj.get("duration") or 0)),
            "geometry": trip_obj.get("geometry"),
            "waypoint_order": ordered_idx,
            "raw": data,
        }
    except Exception as exc:
        return heuristic_trip(points, roundtrip=roundtrip, error=str(exc))


def route(points: list[dict[str, Any]]) -> dict:
    """Fixed-order OSRM Route between points."""
    if len(points) < 2:
        raise ValueError("At least two coordinates are required for a route.")
    url = f"{_base_url()}/route/v1/{_profile()}/{_coords_param(points)}"
    params = {"geometries": "geojson", "overview": "full", "steps": "false"}
    try:
        resp = requests.get(url, params=params, timeout=_timeout())
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            raise RuntimeError(data.get("message") or "OSRM route failed")
        route_obj = data["routes"][0]
        return {
            "engine": "OSRM",
            "distance_m": int(round(route_obj.get("distance") or 0)),
            "duration_s": int(round(route_obj.get("duration") or 0)),
            "geometry": route_obj.get("geometry"),
            "waypoint_order": list(range(len(points))),
            "raw": data,
        }
    except Exception as exc:
        return heuristic_route(points, error=str(exc))


def heuristic_trip(points, roundtrip=True, error=None):
    """Nearest-neighbor fallback when OSRM is offline — still risk-order aware if pre-sorted."""
    if not points:
        return {"engine": "heuristic", "distance_m": 0, "duration_s": 0, "geometry": None, "waypoint_order": [], "error": error}
    remaining = list(range(1, len(points)))
    order = [0]
    while remaining:
        last = points[order[-1]]
        nxt = min(remaining, key=lambda i: haversine_m(last["lat"], last["lng"], points[i]["lat"], points[i]["lng"]))
        remaining.remove(nxt)
        order.append(nxt)
    if roundtrip:
        order.append(0)
    return _finalize_heuristic(points, order, error)


def heuristic_route(points, error=None):
    order = list(range(len(points)))
    return _finalize_heuristic(points, order, error)


def _finalize_heuristic(points, order, error=None):
    coords = []
    distance = 0.0
    for a, b in zip(order, order[1:]):
        distance += haversine_m(points[a]["lat"], points[a]["lng"], points[b]["lat"], points[b]["lng"])
        coords.append([points[a]["lng"], points[a]["lat"]])
    if order:
        last = points[order[-1]]
        coords.append([last["lng"], last["lat"]])
    # ~1.3 m/s walking
    duration = int(distance / 1.3) if distance else 0
    geometry = {"type": "LineString", "coordinates": coords} if len(coords) >= 2 else None
    return {
        "engine": "heuristic",
        "distance_m": int(round(distance)),
        "duration_s": duration,
        "geometry": geometry,
        "waypoint_order": order[:-1] if order and order[0] == order[-1] else order,
        "error": error,
    }
