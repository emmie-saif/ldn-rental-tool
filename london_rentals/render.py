"""Render the active listings as a single HTML page."""
from __future__ import annotations
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from london_rentals import config, dedup
from london_rentals.geo import closer_gym

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

# HMO / flatshare giveaways. A listing whose description matches any of these
# is a room in a shared house, not a whole property — exclude from the page.
HMO_EXCLUSION_PATTERNS = [
    re.compile(r"rooms?\s+already\s+let", re.I),
    re.compile(r"\bavailable\s+rooms?\b", re.I),
]


def _is_hmo(description: Optional[str]) -> bool:
    if not description:
        return False
    return any(p.search(description) for p in HMO_EXCLUSION_PATTERNS)


def _field(row: sqlite3.Row, name: str) -> Optional[str]:
    """Safe getter for sqlite3.Row — returns None if the column isn't present."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


@dataclass
class Card:
    bucket: str
    source: str
    source_id: str
    url: str
    title: Optional[str]
    address: Optional[str]
    postcode: Optional[str]
    price_pcm: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    available_from: Optional[str]
    bathtub: str
    outdoor: str
    amenities: list[str]
    closer_gym_name: Optional[str]
    closer_gym_minutes: Optional[int]
    closer_gym_distance_m: Optional[int]
    # extra_destinations is a list of {name, minutes, distance_m} dicts —
    # one entry per config.DISPLAY_DESTINATIONS (e.g. Kings Cross, LISA).
    extra_destinations: list[dict] = field(default_factory=list)
    first_seen_utc: str = ""
    alternates: list[dict] = field(default_factory=list)


@dataclass
class RemovedCard:
    source: str
    source_id: str
    url: str
    address: Optional[str]
    price_pcm: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    removed_utc: str


def _bucket(beds: Optional[int], baths: Optional[int]) -> Optional[str]:
    if beds == 0:
        return "studio"
    if beds == 1:
        return "1bed_1bath"
    if beds == 2:
        if baths and baths >= 2:
            return "2bed_2bath"
        return "2bed_1bath"
    if beds == 3:
        if baths and baths >= 3:
            return "3bed_3bath"
        if baths and baths >= 2:
            return "3bed_2bath"
        # 3-bed/1-bath isn't a requested category — drop.
        return None
    return None


def _route(conn: sqlite3.Connection, lat: float, lng: float, dest_key: str) -> Optional[tuple[int, int]]:
    lat_r = round(lat, config.ROUTE_CACHE_DP)
    lng_r = round(lng, config.ROUTE_CACHE_DP)
    row = conn.execute(
        "SELECT duration_s, distance_m FROM route_cache WHERE lat_round = ? AND lng_round = ? AND destination = ?",
        (lat_r, lng_r, dest_key),
    ).fetchone()
    if row is None:
        return None
    return (row["duration_s"], row["distance_m"])


def build_cards(conn: sqlite3.Connection) -> dict[str, list[Card]]:
    """Return cards bucketed by category, each list sorted by closer-gym minutes."""
    buckets: dict[str, list[Card]] = {
        "3bed_3bath": [],
        "3bed_2bath": [],
        "2bed_2bath": [],
        "2bed_1bath": [],
        "1bed_1bath": [],
        "studio": [],
    }
    canon = conn.execute(
        """
        SELECT l.*
          FROM listings l
          JOIN clusters c ON c.canonical_source = l.source AND c.canonical_source_id = l.source_id
         WHERE l.removed_utc IS NULL
           AND l.lat IS NOT NULL AND l.lng IS NOT NULL
        """
    ).fetchall()
    for row in canon:
        bkt = _bucket(row["bedrooms"], row["bathrooms"])
        if bkt is None:
            continue
        # Filter out HMOs / room-shares masquerading as multi-bed flats.
        if _is_hmo(_field(row, "description")):
            continue
        gym = closer_gym(row["lat"], row["lng"])
        gym_route = _route(conn, row["lat"], row["lng"], gym.key)
        feats = json.loads(row["features_json"]) if row["features_json"] else {}
        extra_destinations = []
        for dest in config.DISPLAY_DESTINATIONS:
            r = _route(conn, row["lat"], row["lng"], dest.key)
            extra_destinations.append({
                "name": dest.name,
                "minutes": round(r[0] / 60) if r else None,
                "distance_m": r[1] if r else None,
            })
        alternates = [
            {
                "source": a["source"],
                "url": a["url"],
                "price_pcm": a["price_pcm"],
            }
            for a in dedup.cluster_alternates(conn, row["cluster_id"], (row["source"], row["source_id"]))
        ]
        buckets[bkt].append(Card(
            bucket=bkt,
            source=row["source"],
            source_id=row["source_id"],
            url=row["url"],
            title=None,
            address=row["address"],
            postcode=row["postcode"],
            price_pcm=row["price_pcm"],
            bedrooms=row["bedrooms"],
            bathrooms=row["bathrooms"],
            available_from=row["available_from"],
            bathtub=feats.get("bathtub", "unknown"),
            outdoor=feats.get("outdoor", "unknown"),
            amenities=feats.get("amenities", []),
            closer_gym_name=gym.name,
            closer_gym_minutes=round(gym_route[0] / 60) if gym_route else None,
            closer_gym_distance_m=gym_route[1] if gym_route else None,
            extra_destinations=extra_destinations,
            first_seen_utc=row["first_seen_utc"],
            alternates=alternates,
        ))
    for k in buckets:
        buckets[k].sort(key=lambda c: (
            c.closer_gym_minutes if c.closer_gym_minutes is not None else 999,
            c.first_seen_utc,
        ))
    return buckets


def build_removed(conn: sqlite3.Connection, days: int = 1) -> list[RemovedCard]:
    rows = conn.execute(
        """
        SELECT * FROM listings
         WHERE removed_utc IS NOT NULL
           AND removed_utc >= datetime('now', ?)
         ORDER BY removed_utc DESC
        """,
        (f"-{days} day",),
    ).fetchall()
    return [
        RemovedCard(
            source=r["source"],
            source_id=r["source_id"],
            url=r["url"],
            address=r["address"],
            price_pcm=r["price_pcm"],
            bedrooms=r["bedrooms"],
            bathrooms=r["bathrooms"],
            removed_utc=r["removed_utc"],
        )
        for r in rows
    ]


def render(conn: sqlite3.Connection, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    buckets = build_cards(conn)
    removed = build_removed(conn, days=1)
    total = sum(len(v) for v in buckets.values())
    html = env.get_template("index.html.j2").render(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        buckets=buckets,
        removed=removed,
        total=total,
        rent_ceiling=config.RENT_CEILING_PCM,
        gyms=config.GYMS,
    )
    out_path = out_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Rendered %d cards to %s", total, out_path)
    return out_path
