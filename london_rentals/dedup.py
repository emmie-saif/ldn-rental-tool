"""Cluster listings across sources.

Cluster key:
    (round(lat, 3), round(lng, 3), bedrooms, price_band)

3dp lat/lng ≈ 110m (house-block resolution); price_band groups prices by £50.
Two listings hitting the same key are very likely the same flat. We don't drop
duplicates: the canonical (cheapest active) is shown in the main card and the
others are listed as "also on X for £Y" below it.
"""
from __future__ import annotations
import sqlite3
from typing import Optional

from london_rentals import config


def cluster_key(lat: float, lng: float, bedrooms: Optional[int], price_pcm: Optional[int]) -> str:
    lat_r = round(lat, config.CLUSTER_LATLNG_DP)
    lng_r = round(lng, config.CLUSTER_LATLNG_DP)
    band = (price_pcm or 0) // config.CLUSTER_PRICE_BAND
    beds = bedrooms if bedrooms is not None else -1
    return f"{lat_r:.3f}|{lng_r:.3f}|{beds}|{band}"


def assign_clusters(conn: sqlite3.Connection) -> None:
    """Recompute cluster_id for every active listing and refresh canonical members."""
    rows = conn.execute(
        """
        SELECT source, source_id, lat, lng, bedrooms, price_pcm
          FROM listings
         WHERE removed_utc IS NULL
           AND lat IS NOT NULL AND lng IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        cid = cluster_key(row["lat"], row["lng"], row["bedrooms"], row["price_pcm"])
        conn.execute(
            "UPDATE listings SET cluster_id = ? WHERE source = ? AND source_id = ?",
            (cid, row["source"], row["source_id"]),
        )
    # Refresh clusters table: pick cheapest active listing per cluster as canonical.
    conn.execute("DELETE FROM clusters")
    canon_rows = conn.execute(
        """
        SELECT cluster_id, source, source_id, price_pcm
          FROM (
            SELECT cluster_id, source, source_id, price_pcm,
                   ROW_NUMBER() OVER (
                     PARTITION BY cluster_id
                     ORDER BY price_pcm ASC, first_seen_utc ASC
                   ) AS rn
              FROM listings
             WHERE removed_utc IS NULL AND cluster_id IS NOT NULL
          )
         WHERE rn = 1
        """
    ).fetchall()
    for r in canon_rows:
        conn.execute(
            "INSERT INTO clusters (cluster_id, canonical_source, canonical_source_id) VALUES (?, ?, ?)",
            (r["cluster_id"], r["source"], r["source_id"]),
        )
    conn.commit()


def cluster_alternates(conn: sqlite3.Connection, cluster_id: str, exclude: tuple[str, str]) -> list[sqlite3.Row]:
    """Other (non-canonical) active listings in the same cluster, cheapest first."""
    return list(conn.execute(
        """
        SELECT source, source_id, url, price_pcm
          FROM listings
         WHERE cluster_id = ?
           AND removed_utc IS NULL
           AND NOT (source = ? AND source_id = ?)
         ORDER BY price_pcm ASC
        """,
        (cluster_id, exclude[0], exclude[1]),
    ))
