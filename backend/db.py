"""
SQLite database for persistent data.
- interests: user marks (interesting / not interesting)
- listings:  scraped property results with first_seen / last_seen tracking
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "house_finder.db"
STALE_DAYS = 30  # drop listings not seen in this many days


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist yet. Called once at startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interests (
                link      TEXT PRIMARY KEY,
                status    TEXT NOT NULL,
                marked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                link          TEXT PRIMARY KEY,
                source        TEXT NOT NULL,
                title         TEXT,
                price         INTEGER,
                price_text    TEXT,
                location      TEXT,
                postcode      TEXT,
                street        TEXT,
                bedrooms      INTEGER,
                sqm           INTEGER,
                garden        INTEGER,
                garden_sqm    INTEGER,
                image_url     TEXT,
                property_type TEXT,
                listed_date   TEXT,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL
            )
        """)
        # Migrate existing DBs that predate new columns
        for col_def in ["street TEXT", "garden INTEGER", "garden_sqm INTEGER"]:
            try:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {col_def}")
            except Exception:
                pass
        conn.commit()


# ── Interests ──────────────────────────────────────────────────────────────

def get_all_interests() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT link, status FROM interests").fetchall()
    return {row["link"]: row["status"] for row in rows}


def set_interest(link: str, status: Optional[str]):
    with _connect() as conn:
        if status is None:
            conn.execute("DELETE FROM interests WHERE link = ?", (link,))
        else:
            conn.execute(
                """
                INSERT INTO interests (link, status)
                VALUES (?, ?)
                ON CONFLICT(link) DO UPDATE
                    SET status    = excluded.status,
                        marked_at = CURRENT_TIMESTAMP
                """,
                (link, status),
            )
        conn.commit()


# ── Listings ───────────────────────────────────────────────────────────────

def upsert_listings(results: list) -> None:
    """
    Insert new listings or update last_seen for existing ones.
    first_seen is only set on insert — never overwritten.
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        for r in results:
            if not r.link:
                continue
            garden_int = None if r.garden is None else (1 if r.garden else 0)
            conn.execute(
                """
                INSERT INTO listings
                    (link, source, title, price, price_text, location, postcode,
                     street, bedrooms, sqm, garden, garden_sqm,
                     image_url, property_type, listed_date, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(link) DO UPDATE SET
                    title         = excluded.title,
                    price         = excluded.price,
                    price_text    = excluded.price_text,
                    location      = excluded.location,
                    postcode      = excluded.postcode,
                    street        = COALESCE(excluded.street, listings.street),
                    bedrooms      = COALESCE(excluded.bedrooms, listings.bedrooms),
                    sqm           = COALESCE(excluded.sqm, listings.sqm),
                    garden        = COALESCE(excluded.garden, listings.garden),
                    garden_sqm    = COALESCE(excluded.garden_sqm, listings.garden_sqm),
                    image_url     = COALESCE(excluded.image_url, listings.image_url),
                    property_type = COALESCE(excluded.property_type, listings.property_type),
                    listed_date   = COALESCE(listings.listed_date, excluded.listed_date),
                    last_seen     = excluded.last_seen
                """,
                (
                    r.link, r.source, r.title, r.price, r.price_text,
                    r.location, r.postcode, r.street, r.bedrooms, r.sqm,
                    garden_int, r.garden_sqm, r.image_url, r.property_type,
                    r.listed_date, now, now,
                ),
            )
        conn.commit()


def query_listings(
    postcodes: list[str],
    sources: list[str],
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    bedrooms_min: Optional[int] = None,
    sqm_min: Optional[int] = None,
    building_type: Optional[str] = None,
) -> list[dict]:
    """Query cached listings from DB matching the given criteria."""
    clauses = []
    params: list = []

    if postcodes:
        placeholders = ",".join("?" * len(postcodes))
        clauses.append(f"postcode IN ({placeholders})")
        params.extend(postcodes)

    if sources:
        placeholders = ",".join("?" * len(sources))
        clauses.append(f"source IN ({placeholders})")
        params.extend(sources)

    if price_min:
        clauses.append("(price IS NULL OR price >= ?)")
        params.append(price_min)

    if price_max:
        clauses.append("(price IS NULL OR price <= ?)")
        params.append(price_max)

    if bedrooms_min:
        clauses.append("(bedrooms IS NULL OR bedrooms >= ?)")
        params.append(bedrooms_min)

    if sqm_min:
        clauses.append("(sqm IS NULL OR sqm >= ?)")
        params.append(sqm_min)

    if building_type:
        target = building_type.lower()
        if target in ("villa", "townhouse"):
            target = "house"
        clauses.append("(property_type IS NULL OR property_type = ?)")
        params.append(target)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT *, COALESCE(listed_date, first_seen) AS sort_date FROM listings {where} ORDER BY sort_date DESC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_cache_age(sources: list[str], postcodes: list[str]) -> Optional[datetime]:
    """Return the most recent last_seen timestamp for this source+postcode combo."""
    if not sources or not postcodes:
        return None
    src_ph = ",".join("?" * len(sources))
    pc_ph = ",".join("?" * len(postcodes))
    with _connect() as conn:
        row = conn.execute(
            f"SELECT MAX(last_seen) as ts FROM listings WHERE source IN ({src_ph}) AND postcode IN ({pc_ph})",
            sources + postcodes,
        ).fetchone()
    if row and row["ts"]:
        return datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S")
    return None


def drop_stale_listings():
    """Remove listings not seen in STALE_DAYS days."""
    cutoff = (datetime.utcnow() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute("DELETE FROM listings WHERE last_seen < ?", (cutoff,))
        conn.commit()
