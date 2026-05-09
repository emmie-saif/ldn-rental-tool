"""Orchestrator: scrape -> filter -> extract -> route -> dedup -> render."""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

from london_rentals import config, db, dedup, geo, render
from london_rentals.extract import extract_features
from london_rentals.geo import OrsCallCounter
from london_rentals.sources import ALL_SOURCES, Source
from london_rentals.sources.base import Listing

log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="london-rentals.run")
    p.add_argument("--dry-run", action="store_true", help="Don't write DB, don't render")
    p.add_argument("--no-render", action="store_true", help="Skip HTML render step")
    p.add_argument("--site-dir", default="site", help="Output directory for HTML")
    p.add_argument("--db-path", default=str(db.DB_PATH), help="SQLite path")
    return p.parse_args(argv)


def selected_outcodes() -> list[str]:
    raw = os.environ.get("LIMIT_OUTCODES", "").strip()
    if not raw:
        return list(config.OUTCODES)
    return [o.strip().upper() for o in raw.split(",") if o.strip()]


def selected_sources() -> list[type[Source]]:
    raw = os.environ.get("LIMIT_SOURCES", "").strip()
    if not raw:
        return list(ALL_SOURCES)
    wanted = {s.strip().lower() for s in raw.split(",") if s.strip()}
    return [s for s in ALL_SOURCES if s.name.lower() in wanted]


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = parse_args(argv if argv is not None else sys.argv[1:])

    ors_key = os.environ.get("ORS_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    nominatim_ua = os.environ.get("NOMINATIM_USER_AGENT", config.NOMINATIM_DEFAULT_UA)

    if not ors_key:
        log.error("ORS_API_KEY not set; cannot compute isochrones or routes")
        return 2

    db_path = Path(args.db_path)
    conn = db.connect(db_path)
    run_id = db.start_run(conn)
    counter = OrsCallCounter()
    errors: list[str] = []
    source_counts: dict[str, int] = defaultdict(int)
    new_count = 0

    try:
        log.info("run %d starting; outcodes=%s sources=%s",
                 run_id, selected_outcodes(),
                 [s.name for s in selected_sources()])
        polys = geo.daily_isochrones(conn, ors_key, counter)
        log.info("isochrones loaded: %s", list(polys.keys()))

        outcodes = selected_outcodes()
        source_classes = selected_sources()
        seen_keys: set[tuple[str, str]] = set()

        for source_cls in source_classes:
            source = source_cls()
            for outcode in outcodes:
                try:
                    candidates = list(source.fetch_outcode(outcode))
                except Exception as exc:
                    msg = f"{source.name}/{outcode} fetch failed: {exc!r}"
                    log.warning(msg)
                    errors.append(msg)
                    continue
                source_counts[source.name] += len(candidates)
                log.info("%s/%s -> %d candidates", source.name, outcode, len(candidates))

                for listing in candidates:
                    key = (listing.source, listing.source_id)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    try:
                        new_count += _process_listing(
                            conn, listing, source, polys, ors_key, anthropic_key,
                            nominatim_ua, counter, args.dry_run,
                        )
                    except Exception as exc:
                        msg = f"{source.name}/{listing.source_id} processing failed: {exc!r}"
                        log.warning(msg)
                        errors.append(msg)

        if not args.dry_run:
            removed = db.sweep_removed(conn, config.REMOVAL_GRACE_DAYS)
            log.info("swept %d listings as removed", removed)
            dedup.assign_clusters(conn)
            if not args.no_render:
                render.render(conn, Path(args.site_dir))

        status = "ok" if not errors else "partial"
        if not source_counts:
            status = "failed"
        db.finish_run(conn, run_id, status, dict(source_counts), counter.calls, errors)
        log.info("run %d done: status=%s ors_calls=%d new_listings=%d",
                 run_id, status, counter.calls, new_count)
        return 0 if status != "failed" else 1
    except Exception as exc:
        log.exception("run %d crashed: %s", run_id, exc)
        errors.append(f"crash: {exc!r}")
        db.finish_run(conn, run_id, "failed", dict(source_counts), counter.calls, errors)
        return 1
    finally:
        conn.close()


def _process_listing(
    conn,
    listing: Listing,
    source: Source,
    polys: dict,
    ors_key: str,
    anthropic_key: str | None,
    nominatim_ua: str,
    counter: OrsCallCounter,
    dry_run: bool,
) -> int:
    """Process one search-result listing. Returns 1 if newly inserted, 0 otherwise."""
    # Cheap pre-filter: known-too-expensive listings get dropped before detail fetch.
    if listing.price_pcm is not None and listing.price_pcm > config.RENT_CEILING_PCM:
        return 0
    existing = conn.execute(
        "SELECT lat, lng, features_json FROM listings WHERE source = ? AND source_id = ?",
        (listing.source, listing.source_id),
    ).fetchone()
    if existing is None:
        listing = source.fetch_detail(listing)
    else:
        listing.lat = existing["lat"]
        listing.lng = existing["lng"]
    # Re-check price after detail fetch.
    if listing.price_pcm is not None and listing.price_pcm > config.RENT_CEILING_PCM:
        return 0
    # Geocode if needed.
    if (listing.lat is None or listing.lng is None) and listing.address:
        coords = geo.geocode(conn, listing.address, nominatim_ua)
        if coords:
            listing.lat, listing.lng = coords
    if listing.lat is None or listing.lng is None:
        return 0
    eligible, _flags = geo.is_eligible(listing.lat, listing.lng, polys)
    if not eligible:
        return 0
    # Routing for new survivors.
    gym = geo.closer_gym(listing.lat, listing.lng)
    geo.bike_route(conn, ors_key, listing.lat, listing.lng, gym, counter)
    geo.bike_route(conn, ors_key, listing.lat, listing.lng, config.KINGS_CROSS, counter)
    # Feature extraction.
    if existing is None:
        feats = extract_features(listing, anthropic_key)
        feats_json = json.dumps(feats)
    else:
        feats_json = existing["features_json"]
    if dry_run:
        log.info("[dry-run] would upsert %s/%s @ %.5f,%.5f price=%s beds=%s baths=%s",
                 listing.source, listing.source_id, listing.lat, listing.lng,
                 listing.price_pcm, listing.bedrooms, listing.bathrooms)
        return 1 if existing is None else 0
    row = {
        "source": listing.source,
        "source_id": listing.source_id,
        "url": listing.url,
        "price_pcm": listing.price_pcm,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "available_from": listing.available_from,
        "postcode": listing.postcode,
        "address": listing.address,
        "lat": listing.lat,
        "lng": listing.lng,
        "raw_json": json.dumps(listing.raw)[:5000],
        "features_json": feats_json,
    }
    inserted = db.upsert_listing(conn, row, db.utc_now_iso())
    conn.commit()
    return 1 if inserted else 0


if __name__ == "__main__":
    raise SystemExit(main())
