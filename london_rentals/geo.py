"""Geo: ORS isochrones + directions, Nominatim geocoder, point-in-polygon, caches.

All ORS / Nominatim calls go through these helpers so rate-limiting and caching
are centralised. Caches live in state.db (geocode_cache, route_cache,
isochrone_cache).
"""
from __future__ import annotations
import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from london_rentals import config
from london_rentals.config import Place
from london_rentals.db import utc_now_iso

log = logging.getLogger(__name__)


class OrsCallCounter:
    """Mutable counter so the orchestrator can read total ORS calls per run."""
    def __init__(self) -> None:
        self.calls = 0

    def inc(self) -> None:
        self.calls += 1


_last_ors_call_at: float = 0.0
_last_nominatim_call_at: float = 0.0


def _throttle(last_at: float, interval: float) -> float:
    """Sleep until interval seconds have passed since last_at. Return new last_at."""
    now = time.monotonic()
    wait = interval - (now - last_at)
    if wait > 0:
        time.sleep(wait)
    return time.monotonic()


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def closer_gym(lat: float, lng: float) -> Place:
    return min(config.GYMS, key=lambda g: haversine_m(lat, lng, g.lat, g.lng))


# ---------- Isochrones ----------

def fetch_isochrone(api_key: str, place: Place, counter: OrsCallCounter) -> dict:
    """Call ORS /v2/isochrones for a single place. Returns the GeoJSON dict."""
    global _last_ors_call_at
    _last_ors_call_at = _throttle(_last_ors_call_at, config.ORS_REQ_INTERVAL_S)
    body = {
        "locations": [[place.lng, place.lat]],
        "range": [place.bike_minutes * 60],
        "range_type": "time",
        "attributes": ["area"],
    }
    r = requests.post(
        config.ORS_BASE + config.ORS_ISOCHRONES_PATH,
        json=body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        },
        timeout=config.HTTP_TIMEOUT_S,
    )
    counter.inc()
    r.raise_for_status()
    return r.json()


def daily_isochrones(
    conn: sqlite3.Connection,
    api_key: str,
    counter: OrsCallCounter,
) -> dict[str, BaseGeometry]:
    """Get today's isochrones, fetching from ORS if not cached. Returns {place.key: polygon}."""
    today = datetime.now(timezone.utc).date().isoformat()
    polys: dict[str, BaseGeometry] = {}
    for place in config.DESTINATIONS:
        cache_key = f"{place.key}_{place.bike_minutes}"
        row = conn.execute(
            "SELECT geojson FROM isochrone_cache WHERE date = ? AND key = ?",
            (today, cache_key),
        ).fetchone()
        if row is not None:
            geojson = json.loads(row["geojson"])
        else:
            log.info("Fetching isochrone for %s (%dmin)", place.name, place.bike_minutes)
            geojson = fetch_isochrone(api_key, place, counter)
            conn.execute(
                "INSERT OR REPLACE INTO isochrone_cache (date, key, geojson) VALUES (?, ?, ?)",
                (today, cache_key, json.dumps(geojson)),
            )
            conn.commit()
        feature = geojson["features"][0]
        polys[place.key] = shape(feature["geometry"])
    return polys


def is_eligible(lat: float, lng: float, polys: dict[str, BaseGeometry]) -> tuple[bool, dict[str, bool]]:
    """(in_castle ∨ in_mile_end) ∧ in_kings_cross. Returns (eligible, per-place dict)."""
    pt = Point(lng, lat)
    flags = {key: bool(poly.contains(pt)) for key, poly in polys.items()}
    eligible = (flags.get(config.CASTLE.key, False) or flags.get(config.MILE_END.key, False)) \
        and flags.get(config.KINGS_CROSS.key, False)
    return eligible, flags


# ---------- Directions ----------

@dataclass
class Route:
    duration_s: int
    distance_m: int

    @property
    def minutes(self) -> int:
        return round(self.duration_s / 60)


def _route_cache_get(conn: sqlite3.Connection, lat_r: float, lng_r: float, dest: str) -> Optional[Route]:
    row = conn.execute(
        "SELECT duration_s, distance_m, fetched_utc FROM route_cache WHERE lat_round = ? AND lng_round = ? AND destination = ?",
        (lat_r, lng_r, dest),
    ).fetchone()
    if row is None:
        return None
    fetched = datetime.fromisoformat(row["fetched_utc"])
    if datetime.now(timezone.utc) - fetched > timedelta(days=config.ROUTE_CACHE_TTL_DAYS):
        return None
    return Route(duration_s=row["duration_s"], distance_m=row["distance_m"])


def _route_cache_put(conn: sqlite3.Connection, lat_r: float, lng_r: float, dest: str, r: Route) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO route_cache (lat_round, lng_round, destination, duration_s, distance_m, fetched_utc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (lat_r, lng_r, dest, r.duration_s, r.distance_m, utc_now_iso()),
    )


def bike_route(
    conn: sqlite3.Connection,
    api_key: str,
    lat: float,
    lng: float,
    dest: Place,
    counter: OrsCallCounter,
) -> Route:
    """Cycling route from (lat, lng) to dest. Cached by rounded coordinates."""
    global _last_ors_call_at
    lat_r = round(lat, config.ROUTE_CACHE_DP)
    lng_r = round(lng, config.ROUTE_CACHE_DP)
    cached = _route_cache_get(conn, lat_r, lng_r, dest.key)
    if cached is not None:
        return cached
    _last_ors_call_at = _throttle(_last_ors_call_at, config.ORS_REQ_INTERVAL_S)
    body = {"coordinates": [[lng, lat], [dest.lng, dest.lat]]}
    r = requests.post(
        config.ORS_BASE + config.ORS_DIRECTIONS_PATH,
        json=body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=config.HTTP_TIMEOUT_S,
    )
    counter.inc()
    r.raise_for_status()
    summary = r.json()["routes"][0]["summary"]
    route = Route(duration_s=int(summary["duration"]), distance_m=int(summary["distance"]))
    _route_cache_put(conn, lat_r, lng_r, dest.key, route)
    conn.commit()
    return route


# ---------- Geocoding ----------

def _norm_address(address: str) -> str:
    return " ".join(address.lower().split())


def geocode(
    conn: sqlite3.Connection,
    address: str,
    user_agent: str,
) -> Optional[tuple[float, float]]:
    """Geocode via Nominatim. Cached per normalised address."""
    global _last_nominatim_call_at
    if not address:
        return None
    norm = _norm_address(address)
    row = conn.execute(
        "SELECT lat, lng FROM geocode_cache WHERE address_norm = ?",
        (norm,),
    ).fetchone()
    if row is not None:
        if row["lat"] is None:
            return None
        return (row["lat"], row["lng"])
    _last_nominatim_call_at = _throttle(_last_nominatim_call_at, config.NOMINATIM_REQ_INTERVAL_S)
    try:
        r = requests.get(
            config.NOMINATIM_BASE,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "gb"},
            headers={"User-Agent": user_agent},
            timeout=config.HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        results = r.json()
    except Exception as exc:
        log.warning("Nominatim error for %r: %s", address, exc)
        return None
    if not results:
        conn.execute(
            "INSERT OR REPLACE INTO geocode_cache (address_norm, lat, lng, fetched_utc) VALUES (?, NULL, NULL, ?)",
            (norm, utc_now_iso()),
        )
        conn.commit()
        return None
    lat = float(results[0]["lat"])
    lng = float(results[0]["lon"])
    conn.execute(
        "INSERT OR REPLACE INTO geocode_cache (address_norm, lat, lng, fetched_utc) VALUES (?, ?, ?, ?)",
        (norm, lat, lng, utc_now_iso()),
    )
    conn.commit()
    return (lat, lng)
