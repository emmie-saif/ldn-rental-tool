"""Static configuration: coordinates, outcodes, endpoints, thresholds."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    key: str
    name: str
    lat: float
    lng: float
    bike_minutes: int


CASTLE = Place("castle", "Castle Climbing Centre", 51.5722, -0.0863, 10)
MILE_END = Place("mile_end", "Mile End Climbing Wall", 51.5266, -0.0298, 10)
KINGS_CROSS = Place("kings_cross", "Kings Cross", 51.5308, -0.1238, 30)

GYMS: list[Place] = [CASTLE, MILE_END]
DESTINATIONS: list[Place] = [CASTLE, MILE_END, KINGS_CROSS]

RENT_CEILING_PCM = 3500
MAX_BEDROOMS = 2

# London outcodes that overlap the eligible region (the union of 10-min cycling
# isochrones around both gyms). Each source is queried once per outcode; the
# polygon checks downstream do the precise filtering.
OUTCODES: list[str] = [
    "N1", "N4", "N5", "N7", "N16", "N19",
    "E1", "E2", "E3", "E5", "E8", "E9", "E14",
    "EC1", "WC1",
]

# OpenRouteService.
ORS_BASE = "https://api.openrouteservice.org"
ORS_BIKE_PROFILE = "cycling-regular"
ORS_DIRECTIONS_PATH = f"/v2/directions/{ORS_BIKE_PROFILE}"
ORS_ISOCHRONES_PATH = f"/v2/isochrones/{ORS_BIKE_PROFILE}"
ORS_REQ_INTERVAL_S = 1.6  # under the 40 req/min free-tier cap

# Nominatim.
NOMINATIM_BASE = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REQ_INTERVAL_S = 1.05  # respect the 1 req/sec rule
NOMINATIM_DEFAULT_UA = "london-rentals/1.0 (personal-use)"

# Anthropic.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_MAX_TOKENS = 400

# Sweep window: a listing unseen for this many days is marked removed.
REMOVAL_GRACE_DAYS = 3

# Route cache: lat/lng rounded to this many decimal places (4dp ≈ 11m).
ROUTE_CACHE_DP = 4
ROUTE_CACHE_TTL_DAYS = 30

# Dedup cluster key precision.
CLUSTER_LATLNG_DP = 3  # ~110m, house-block resolution
CLUSTER_PRICE_BAND = 500  # £500 buckets — same flat across sources rarely differs by more

# Rightmove RSS feed URLs. Populate with one URL per outcode after creating
# saved searches in a throwaway Rightmove account; format:
#   https://www.rightmove.co.uk/property-to-rent/find.html?...&_xml=true
RIGHTMOVE_RSS_FEEDS: dict[str, str] = {
    # "N4": "https://www.rightmove.co.uk/...",
}

# HTTP behaviour.
HTTP_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) london-rentals/1.0"
HTTP_TIMEOUT_S = 30
