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
KINGS_CROSS = Place("kings_cross", "Kings Cross", 51.5308, -0.1238, 35)

# LISA — London Initiative for Safe AI. 25 Holywell Row, Shoreditch, EC2A 4XE.
# Coordinates verified against the OpenStreetMap entry for "London Initiative
# for Safe AI" (office/coworking). Display-only destination — we route to it
# and show the time on each card, but don't filter on distance.
LISA = Place("lisa", "LISA", 51.5228306, -0.0821032, 0)

GYMS: list[Place] = [CASTLE, MILE_END]
# Destinations we compute isochrones for (used for in/out filtering).
ISOCHRONE_DESTINATIONS: list[Place] = [CASTLE, MILE_END, KINGS_CROSS]
# Destinations we route to per-listing AND show on the card (KC + LISA).
DISPLAY_DESTINATIONS: list[Place] = [KINGS_CROSS, LISA]
# Backwards-compat alias for any callers still using DESTINATIONS.
DESTINATIONS = ISOCHRONE_DESTINATIONS

# Rent ceiling is per-bedroom: 3-beds get a slightly higher cap.
RENT_CEILINGS_BY_BEDS: dict[int, int] = {
    0: 3500,
    1: 3500,
    2: 3500,
    3: 4000,
}
MAX_RENT_CEILING_PCM = max(RENT_CEILINGS_BY_BEDS.values())  # used at the source-side filter
MAX_BEDROOMS = 3

def rent_ceiling_for(bedrooms: int | None) -> int:
    """Per-bedroom rent ceiling. Returns the loosest cap if bedrooms unknown."""
    if bedrooms is None:
        return MAX_RENT_CEILING_PCM
    return RENT_CEILINGS_BY_BEDS.get(bedrooms, MAX_RENT_CEILING_PCM)

# Backwards-compat: anywhere RENT_CEILING_PCM is still referenced gets the loose cap.
RENT_CEILING_PCM = MAX_RENT_CEILING_PCM

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

# Rightmove location identifiers. Each outcode maps to "OUTCODE^<id>" or, for
# central districts that span multiple outcodes (EC1, WC1), "REGION^<id>".
# Values verified via los.rightmove.co.uk/typeahead. Stable enough to hardcode.
RIGHTMOVE_LOCATION_IDS: dict[str, str] = {
    "N1":  "OUTCODE^1666",
    "N4":  "OUTCODE^1682",
    "N5":  "OUTCODE^1683",
    "N7":  "OUTCODE^1685",
    "N16": "OUTCODE^1673",
    "N19": "OUTCODE^1676",
    "E1":  "OUTCODE^744",
    "E2":  "OUTCODE^755",
    "E3":  "OUTCODE^756",
    "E5":  "OUTCODE^758",
    "E8":  "OUTCODE^762",
    "E9":  "OUTCODE^763",
    "E14": "OUTCODE^749",
    "EC1": "REGION^91983",
    "WC1": "REGION^91992",
}

# Deprecated — kept so existing imports don't break. Use RIGHTMOVE_LOCATION_IDS.
RIGHTMOVE_RSS_FEEDS: dict[str, str] = {}

# HTTP behaviour. Use a fully realistic browser fingerprint — GH Actions IPs
# get flagged otherwise (sources have returned 405 with a giveaway UA).
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
HTTP_BROWSER_HEADERS = {
    "User-Agent": HTTP_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
HTTP_TIMEOUT_S = 30
