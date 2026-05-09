"""Offline smoke test: validates schema, dedup, render against fixture data.

Skips anything network-dependent (ORS, Nominatim, scrapers, LLM). Exercises
the parts we can run without API keys: DB schema, upsert, sweep, dedup,
render. Run with `python -m tests.smoke_test`.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

# Allow running without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from london_rentals import db, dedup, render
from london_rentals.extract import extract_features
from london_rentals.sources.base import Listing


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "state.db"
    site_dir = Path(tmp.name) / "site"

    conn = db.connect(db_path)
    now = db.utc_now_iso()
    fixtures = [
        {
            "source": "openrent", "source_id": "1001",
            "url": "https://www.openrent.co.uk/property/1001",
            "price_pcm": 2400, "bedrooms": 1, "bathrooms": 1,
            "available_from": "01/06/2026",
            "postcode": "N16 8AB", "address": "Stoke Newington Church St, N16",
            "lat": 51.5618, "lng": -0.0758,
            "raw_json": "{}",
            "features_json": json.dumps({"bathtub": "yes", "outdoor": "balcony", "amenities": ["lift"]}),
        },
        {
            "source": "openrent", "source_id": "1002",
            "url": "https://www.openrent.co.uk/property/1002",
            "price_pcm": 2950, "bedrooms": 2, "bathrooms": 2,
            "available_from": "now",
            "postcode": "E3 5BE", "address": "Mile End Rd, E3",
            "lat": 51.5258, "lng": -0.0319,
            "raw_json": "{}",
            "features_json": json.dumps({"bathtub": "no", "outdoor": "garden", "amenities": ["concierge", "gym"]}),
        },
        {
            "source": "spareroom", "source_id": "9001",
            "url": "https://www.spareroom.co.uk/flatshare/9001",
            "price_pcm": 2390, "bedrooms": 1, "bathrooms": 1,
            "available_from": "now",
            "postcode": "N16 8AB", "address": "Stoke Newington Church St, N16",
            "lat": 51.5618, "lng": -0.0758,  # duplicate of openrent/1001
            "raw_json": "{}",
            "features_json": json.dumps({"bathtub": "unknown", "outdoor": "balcony", "amenities": []}),
        },
        {
            "source": "openrent", "source_id": "1003",
            "url": "https://www.openrent.co.uk/property/1003",
            "price_pcm": 1700, "bedrooms": 0, "bathrooms": 1,
            "available_from": "15/05/2026",
            "postcode": "E5 9AA", "address": "Lower Clapton Rd, E5",
            "lat": 51.5560, "lng": -0.0540,
            "raw_json": "{}",
            "features_json": json.dumps({"bathtub": "unknown", "outdoor": "none", "amenities": []}),
        },
    ]
    for row in fixtures:
        db.upsert_listing(conn, row, now)

    # Fake route_cache rows so render() can fill in the bike-time fields.
    routes = [
        # (lat, lng, dest, dur_s, dist_m)
        (51.5618, -0.0758, "castle",      4 * 60,    900),
        (51.5618, -0.0758, "kings_cross", 14 * 60, 3500),
        (51.5258, -0.0319, "mile_end",    2 * 60,    400),
        (51.5258, -0.0319, "kings_cross", 22 * 60, 5800),
        (51.5560, -0.0540, "castle",      6 * 60,   1500),
        (51.5560, -0.0540, "kings_cross", 18 * 60, 4500),
    ]
    for lat, lng, dest, dur, dist in routes:
        conn.execute(
            "INSERT INTO route_cache (lat_round, lng_round, destination, duration_s, distance_m, fetched_utc) VALUES (?, ?, ?, ?, ?, ?)",
            (round(lat, 4), round(lng, 4), dest, dur, dist, now),
        )
    conn.commit()

    dedup.assign_clusters(conn)
    out_path = render.render(conn, site_dir)

    html = out_path.read_text()
    expected_phrases = [
        "London rentals",
        "2 bed · 2 bath",
        "1 bed · 1 bath",
        "Studio",
        "Stoke Newington",
        "Mile End",
        "Lower Clapton",
        "concierge",
        "also on",  # cluster alternates link present
    ]
    missing = [p for p in expected_phrases if p not in html]
    if missing:
        print(f"FAIL: missing expected phrases: {missing}")
        return 1

    # Also exercise extract_features on a synthetic listing.
    synthetic = Listing(
        source="test", source_id="x", url="http://x",
        title="Lovely 1-bed flat",
        description="Spacious living room with a balcony. Bathroom features a shower, no bath. Concierge service.",
        structured_features=["Lift", "Balcony"],
    )
    feats = extract_features(synthetic, anthropic_key=None)
    assert feats["bathtub"] == "no", feats
    assert feats["outdoor"] == "balcony", feats
    assert "lift" in feats["amenities"], feats
    assert "concierge" in feats["amenities"], feats

    print(f"OK · rendered {out_path} ({len(html)} bytes) · extract features: {feats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
