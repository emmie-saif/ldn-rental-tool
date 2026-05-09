"""SQLite state. One file: state.db. Schema migrations are forward-only."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  source            TEXT NOT NULL,
  source_id         TEXT NOT NULL,
  url               TEXT NOT NULL,
  first_seen_utc    TEXT NOT NULL,
  last_seen_utc     TEXT NOT NULL,
  removed_utc       TEXT,
  price_pcm         INTEGER,
  bedrooms          INTEGER,
  bathrooms         INTEGER,
  available_from    TEXT,
  postcode          TEXT,
  address           TEXT,
  lat               REAL,
  lng               REAL,
  raw_json          TEXT,
  features_json     TEXT,
  cluster_id        TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(removed_utc, cluster_id);
CREATE INDEX IF NOT EXISTS idx_listings_cluster ON listings(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id            TEXT PRIMARY KEY,
  canonical_source      TEXT,
  canonical_source_id   TEXT
);

CREATE TABLE IF NOT EXISTS route_cache (
  lat_round     REAL,
  lng_round     REAL,
  destination   TEXT,
  duration_s    INTEGER,
  distance_m    INTEGER,
  fetched_utc   TEXT,
  PRIMARY KEY (lat_round, lng_round, destination)
);

CREATE TABLE IF NOT EXISTS isochrone_cache (
  date    TEXT,
  key     TEXT,
  geojson TEXT,
  PRIMARY KEY (date, key)
);

CREATE TABLE IF NOT EXISTS geocode_cache (
  address_norm  TEXT PRIMARY KEY,
  lat           REAL,
  lng           REAL,
  fetched_utc   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  started_utc         TEXT NOT NULL,
  finished_utc        TEXT,
  status              TEXT,
  source_counts_json  TEXT,
  ors_calls           INTEGER DEFAULT 0,
  errors_json         TEXT
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def upsert_listing(conn: sqlite3.Connection, row: dict, now: str) -> bool:
    """Insert or update a listing. Returns True if newly inserted."""
    existing = conn.execute(
        "SELECT first_seen_utc FROM listings WHERE source = ? AND source_id = ?",
        (row["source"], row["source_id"]),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO listings (source, source_id, url, first_seen_utc, last_seen_utc,
                                  price_pcm, bedrooms, bathrooms, available_from,
                                  postcode, address, lat, lng, raw_json, features_json)
            VALUES (:source, :source_id, :url, :now, :now,
                    :price_pcm, :bedrooms, :bathrooms, :available_from,
                    :postcode, :address, :lat, :lng, :raw_json, :features_json)
            """,
            {**row, "now": now},
        )
        return True
    conn.execute(
        """
        UPDATE listings
           SET last_seen_utc  = :now,
               url            = :url,
               price_pcm      = :price_pcm,
               bedrooms       = :bedrooms,
               bathrooms      = :bathrooms,
               available_from = :available_from,
               postcode       = :postcode,
               address        = :address,
               lat            = :lat,
               lng            = :lng,
               raw_json       = :raw_json,
               features_json  = COALESCE(:features_json, features_json),
               removed_utc    = NULL
         WHERE source = :source AND source_id = :source_id
        """,
        {**row, "now": now},
    )
    return False


def sweep_removed(conn: sqlite3.Connection, grace_days: int) -> int:
    """Mark listings as removed if last_seen_utc is older than grace_days. Returns count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=grace_days)).isoformat(timespec="seconds")
    cur = conn.execute(
        """
        UPDATE listings
           SET removed_utc = ?
         WHERE removed_utc IS NULL
           AND last_seen_utc < ?
        """,
        (utc_now_iso(), cutoff),
    )
    return cur.rowcount


def active_listings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM listings WHERE removed_utc IS NULL ORDER BY first_seen_utc DESC"
    ))


def recently_removed(conn: sqlite3.Connection, days: int = 1) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return list(conn.execute(
        "SELECT * FROM listings WHERE removed_utc IS NOT NULL AND removed_utc >= ? ORDER BY removed_utc DESC",
        (cutoff,),
    ))


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_utc, status) VALUES (?, ?)",
        (utc_now_iso(), "in_progress"),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    source_counts: dict,
    ors_calls: int,
    errors: list,
) -> None:
    conn.execute(
        """
        UPDATE runs
           SET finished_utc       = ?,
               status             = ?,
               source_counts_json = ?,
               ors_calls          = ?,
               errors_json        = ?
         WHERE run_id = ?
        """,
        (
            utc_now_iso(),
            status,
            json.dumps(source_counts),
            ors_calls,
            json.dumps(errors),
            run_id,
        ),
    )
    conn.commit()
