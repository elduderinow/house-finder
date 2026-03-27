"""
House Finder Belgium - FastAPI backend.
Aggregates property listings from Belgian real estate sites.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

import aiohttp
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .models import SearchCriteria, PropertyResult
from .db import (
    init_db, get_all_interests, set_interest,
    upsert_listings, query_listings, get_cache_age, drop_stale_listings,
)
from .scrapers.immoweb import scrape_immoweb
from .scrapers.zimmo import scrape_zimmo
from .scrapers.immoscoop import scrape_immoscoop
from .scrapers.era import scrape_era
from .scrapers.realo import scrape_realo
from .scrapers.logic_immo import scrape_logic_immo
from .scrapers.heylen import scrape_heylen
from .scrapers.jamar import scrape_jamar

init_db()
drop_stale_listings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("house-finder")

app = FastAPI(
    title="House Finder Belgium",
    description="Aggregated Belgian real estate search",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


CACHE_TTL_MINUTES = 5

_active_scrapes: dict[str, dict] = {}  # scrape_id -> {done, new_count}


class SearchResponse(BaseModel):
    results: list[PropertyResult]
    total: int
    sources: dict[str, int]
    from_cache: bool = False
    cache_age_minutes: Optional[int] = None
    refreshing: bool = False
    scrape_id: Optional[str] = None
    errors: list[str] = []


class InterestUpdate(BaseModel):
    link: str
    status: Optional[str] = None


# ── Interests ──────────────────────────────────────────────────────────────

@app.get("/api/interests")
def get_interests():
    return get_all_interests()


@app.post("/api/interests")
def update_interest(body: InterestUpdate):
    set_interest(body.link, body.status)
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "house-finder-belgium"}


# ── Scraping ───────────────────────────────────────────────────────────────

def _build_tasks(session: aiohttp.ClientSession, criteria: SearchCriteria) -> dict:
    all_tasks = {
        "Immoweb":    scrape_immoweb(session, criteria),
        "Zimmo":      scrape_zimmo(session, criteria),
        "Immoscoop":  scrape_immoscoop(session, criteria),
        "ERA":        scrape_era(session, criteria),
        "Realo":      scrape_realo(session, criteria),
        "Logic Immo": scrape_logic_immo(session, criteria),
        "Heylen":     scrape_heylen(session, criteria),
        "Jamar":      scrape_jamar(session, criteria),
    }
    if criteria.enabled_sources:
        return {k: v for k, v in all_tasks.items() if k in criteria.enabled_sources}
    return all_tasks


async def _run_scrapers(criteria: SearchCriteria) -> tuple[list[PropertyResult], list[str]]:
    """Run all enabled scrapers and return (results, errors)."""
    all_results: list[PropertyResult] = []
    errors: list[str] = []

    async with aiohttp.ClientSession() as session:
        tasks = _build_tasks(session, criteria)
        results_by_source = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for source_name, result in zip(tasks.keys(), results_by_source):
            if isinstance(result, Exception):
                err_msg = f"{source_name}: {type(result).__name__}: {result}"
                logger.error(err_msg)
                errors.append(err_msg)
            elif isinstance(result, list):
                all_results.extend(result)

    return all_results, errors


def _filter_and_sort(
    results: list[PropertyResult],
    criteria: SearchCriteria,
) -> list[PropertyResult]:
    """Deduplicate, filter by postcode + property type."""
    seen: set[str] = set()
    unique: list[PropertyResult] = []
    for r in results:
        if r.link and r.link in seen:
            continue
        if r.link:
            seen.add(r.link)
        unique.append(r)

    if criteria.postcodes:
        postcode_set = set(str(p).strip() for p in criteria.postcodes)
        unique = [r for r in unique if r.postcode in postcode_set]

    if criteria.building_type:
        target = criteria.building_type.lower()
        if target in ("villa", "townhouse"):
            target = "house"
        unique = [r for r in unique if r.property_type is None or r.property_type == target]

    return unique


def _db_rows_to_results(rows: list[dict]) -> list[PropertyResult]:
    results = []
    for row in rows:
        garden_val = row.get("garden")
        results.append(PropertyResult(
            title=row["title"] or "",
            price=row["price"],
            price_text=row["price_text"] or "",
            location=row["location"] or "",
            postcode=row["postcode"] or "",
            street=row.get("street"),
            link=row["link"] or "",
            source=row["source"] or "",
            bedrooms=row["bedrooms"],
            sqm=row["sqm"],
            garden=bool(garden_val) if garden_val is not None else None,
            garden_sqm=row.get("garden_sqm"),
            image_url=row["image_url"],
            property_type=row["property_type"],
            listed_date=row.get("listed_date"),
            first_seen=row.get("first_seen"),
        ))
    return results


async def _background_scrape(scrape_id: str, criteria: SearchCriteria, existing_links: set[str]):
    try:
        raw, _ = await _run_scrapers(criteria)
        filtered = _filter_and_sort(raw, criteria)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for r in filtered:
            if not r.listed_date:
                r.listed_date = today
        upsert_listings(filtered)
        new_count = sum(1 for r in filtered if r.link not in existing_links)
        _active_scrapes[scrape_id] = {"done": True, "new_count": new_count}
        logger.info(f"[BG:{scrape_id[:8]}] Done. {new_count} new listings.")
    except Exception as e:
        logger.error(f"[BG:{scrape_id[:8]}] Error: {e}")
        _active_scrapes[scrape_id] = {"done": True, "new_count": 0}


# ── Search ─────────────────────────────────────────────────────────────────

@app.get("/api/scrape-status/{scrape_id}")
def scrape_status(scrape_id: str):
    job = _active_scrapes.get(scrape_id, {"done": True, "new_count": 0})
    return {"done": job["done"], "new_count": job["new_count"]}


@app.post("/api/search", response_model=SearchResponse)
async def search(criteria: SearchCriteria, background_tasks: BackgroundTasks):
    sources_for_age = criteria.enabled_sources or ["Immoweb", "Zimmo", "ERA", "Realo", "Heylen"]
    cache_ts = get_cache_age(sources_for_age, criteria.postcodes)
    cache_age_min: Optional[int] = None
    if cache_ts:
        cache_age_min = int((datetime.utcnow() - cache_ts).total_seconds() / 60)

    db_rows = query_listings(
        postcodes=criteria.postcodes,
        sources=criteria.enabled_sources or [],
        price_min=criteria.price_min,
        price_max=criteria.price_max,
        bedrooms_min=criteria.bedrooms_min,
        sqm_min=criteria.sqm_min,
        building_type=criteria.building_type,
    )

    if db_rows:
        results = _db_rows_to_results(db_rows)
        is_stale = cache_age_min is None or cache_age_min >= CACHE_TTL_MINUTES
        scrape_id = None
        if is_stale:
            scrape_id = str(uuid.uuid4())
            existing_links = {r.link for r in results}
            _active_scrapes[scrape_id] = {"done": False, "new_count": 0}
            background_tasks.add_task(_background_scrape, scrape_id, criteria, existing_links)

        sources_count: dict[str, int] = {}
        for r in results:
            sources_count[r.source] = sources_count.get(r.source, 0) + 1

        logger.info(f"Serving {len(results)} results from DB (age: {cache_age_min}min, refreshing: {is_stale})")
        return SearchResponse(
            results=results,
            total=len(results),
            sources=sources_count,
            from_cache=True,
            cache_age_minutes=cache_age_min,
            refreshing=is_stale,
            scrape_id=scrape_id,
        )

    # No DB data — scrape live (first time)
    logger.info("No DB data, scraping live…")
    raw, errors = await _run_scrapers(criteria)
    filtered = _filter_and_sort(raw, criteria)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    today = now_str[:10]
    for r in filtered:
        if not r.listed_date:
            r.listed_date = today
        r.first_seen = now_str
    upsert_listings(filtered)

    sources_count = {}
    for r in filtered:
        sources_count[r.source] = sources_count.get(r.source, 0) + 1

    logger.info(f"Live scrape: {len(filtered)} results, upserted to DB")
    return SearchResponse(
        results=filtered,
        total=len(filtered),
        sources=sources_count,
        from_cache=False,
        errors=errors,
    )


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8100,
        reload=False,
    )
