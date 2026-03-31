"""
Scraper for Heylen Vastgoed (heylenvastgoed.be) - major Antwerp real estate agency.

Uses their public JSON API at /api/properties which returns clean structured data.
Filters by postcode and price client-side (API has no such filter params).
Fetches detail for each matched result to retrieve images.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from ..models import SearchCriteria, PropertyResult
from bs4 import BeautifulSoup
from .base import rate_limited_json, get_headers, is_listing_unavailable

logger = logging.getLogger("house-finder.scrapers.heylen")

DOMAIN = "heylenvastgoed.be"

WEBID_MAP = {
    "house": "1",
    "apartment": "2",
    "villa": "1",
    "townhouse": "1",
    "studio": "2",
    "flat": "2",
}

WEBID_TO_TYPE = {
    "1": "house",
    "2": "apartment",
}

WEBID_TO_LABEL = {
    "1": "huis",
    "2": "appartement",
    "4": "grond",
    "5": "opbrengsteigendom",
    "6": "commercieel",
}


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            url, headers=get_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        logger.debug(f"[Heylen] HTML fetch failed for {url}: {e}")
    return None


async def _fetch_json_detail(session: aiohttp.ClientSession, prop_id: int) -> dict:
    url = f"https://www.heylenvastgoed.be/api/properties/{prop_id}/detail"
    try:
        async with session.get(
            url, headers={**get_headers(), "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                raw = await resp.json(content_type=None)
                data = raw.get("data", raw)
                images = data.get("images", data.get("Images", []))
                image_url = None
                if images:
                    images.sort(key=lambda x: x.get("SortOrder", 99))
                    image_url = images[0].get("URL")
                garden_sqm = data.get("SurfaceGarden") or None
                return {"image_url": image_url, "garden_sqm": int(garden_sqm) if garden_sqm else None}
    except Exception as e:
        logger.debug(f"[Heylen] JSON detail fetch failed for {prop_id}: {e}")
    return {}


async def _fetch_detail(session: aiohttp.ClientSession, prop_id: int, listing_url: str) -> dict | None:
    """Fetch JSON detail and verify the HTML listing page is available.

    Returns None if the listing is sold or shows a custom 404 page.
    Returns {} on transient errors (listing kept, extra data missing).
    """
    html, detail = await asyncio.gather(
        _fetch_html(session, listing_url),
        _fetch_json_detail(session, prop_id),
    )

    if html is not None:
        soup = BeautifulSoup(html, "lxml")
        if is_listing_unavailable(soup):
            logger.debug(f"[Heylen] Skipping unavailable listing {prop_id} ({listing_url})")
            return None

    return detail


async def _fetch_details_batch(
    session: aiohttp.ClientSession,
    items: list[dict],
) -> dict[int, dict | None]:
    """Fetch details for multiple properties concurrently.
    Values are None for unavailable listings, {} for fetch errors, dict for success."""
    results = await asyncio.gather(
        *[_fetch_detail(session, item["ID"], item["_listing_url"]) for item in items],
        return_exceptions=True,
    )
    return {
        item["ID"]: (detail if not isinstance(detail, Exception) else {})
        for item, detail in zip(items, results)
    }


def _parse_item(item: dict, detail: Optional[dict] = None) -> Optional[PropertyResult]:
    try:
        detail = detail or {}

        # Skip sold listings based on status name fields
        status_name = (item.get("StatusName") or item.get("StatusText") or "").lower()
        if "verkocht" in status_name or "sold" in status_name:
            return None

        prop_id = item.get("ID", "")
        city = item.get("City", "")
        city_slug = city.lower().replace(" ", "-").replace("'", "")
        postcode = str(item.get("Zip", ""))

        street_name = item.get("Street", "")
        house_number = item.get("HouseNumber", "")
        street = f"{street_name} {house_number}".strip() or None

        price_raw = item.get("Price")
        price = int(float(str(price_raw))) if price_raw else None
        price_text = f"\u20ac{price:,.0f}" if price else "Price on request"

        bedrooms = item.get("NumberOfBedRooms")
        sqm = int(float(str(item["SurfaceTotal"]))) if item.get("SurfaceTotal") else None

        has_garden_buf = item.get("HasGarden", {})
        garden = bool(has_garden_buf.get("data", [0])[0]) if isinstance(has_garden_buf, dict) else None
        garden_sqm = detail.get("garden_sqm")

        web_id = str(item.get("WebID", ""))
        prop_type = WEBID_TO_TYPE.get(web_id)
        type_label = WEBID_TO_LABEL.get(web_id, "eigendom")

        title_parts = [type_label.title()]
        if bedrooms:
            title_parts.append(f"{bedrooms} bed")
        if sqm:
            title_parts.append(f"{sqm}m\u00b2")
        if city:
            title_parts.append(f"in {city}")
        title = " - ".join(title_parts)

        goal = item.get("Goal", 0)
        tx_path = "huren" if goal == 1 else "kopen"
        tx_slug = "te-huur" if goal == 1 else "te-koop"
        link = item.get("_listing_url") or (
            f"https://www.heylenvastgoed.be/{tx_path}/{type_label}-{tx_slug}-in-{city_slug}/{prop_id}"
        )

        listed_date = None
        created = item.get("CreatedDate", "")
        if created and len(created) >= 10:
            listed_date = created[:10]

        return PropertyResult(
            title=title,
            price=price,
            price_text=price_text,
            location=f"{postcode} {city}".strip(),
            postcode=postcode,
            street=street,
            link=link,
            source="Heylen",
            bedrooms=int(bedrooms) if bedrooms else None,
            sqm=sqm,
            garden=garden,
            garden_sqm=garden_sqm,
            image_url=detail.get("image_url"),
            property_type=prop_type,
            listed_date=listed_date,
        )
    except Exception as e:
        logger.debug(f"Heylen item parse error: {e}")
        return None


async def scrape_heylen(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[PropertyResult]:
    """Scrape Heylen Vastgoed via their JSON API, paginating until all results are fetched."""
    goal = 1 if criteria.transaction == "rent" else 0

    base_params: dict = {
        "goal": goal,
        "status": 1,
        "sort": "newest",
        "limit": page_size,
    }

    web_id = WEBID_MAP.get((criteria.building_type or "").lower())
    if web_id:
        base_params["webId"] = web_id

    url = "https://www.heylenvastgoed.be/api/properties"
    items: list[dict] = []

    for page in range(max_pages):
        params = {**base_params, "offset": page * page_size}
        data = await rate_limited_json(session, url, DOMAIN, params=params)

        if not data:
            logger.warning(f"[Heylen] No data returned from API on page {page}")
            break

        page_items = data if isinstance(data, list) else data.get("properties", data.get("data", []))
        if not isinstance(page_items, list):
            logger.warning(f"[Heylen] Unexpected response shape on page {page}: {type(page_items)}")
            break

        items.extend(page_items)

        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        has_more = pagination.get("hasMore", len(page_items) >= page_size)
        logger.debug(f"[Heylen] Page {page}: got {len(page_items)} items (total so far: {len(items)}, hasMore={has_more})")

        if not has_more:
            break

    postcode_set = set(str(p).strip() for p in criteria.postcodes) if criteria.postcodes else None
    price_min = criteria.price_min
    price_max = criteria.price_max

    # Filter items before fetching images
    matched = []
    for item in items:
        # Skip sold/unavailable: Status 1=available, 2=under option, 3=sold
        status = item.get("Status")
        if status is not None and int(status) not in (1, 2):
            continue
        if postcode_set and str(item.get("Zip", "")) not in postcode_set:
            continue
        price_raw = item.get("Price")
        price = int(float(str(price_raw))) if price_raw else None
        if price_min and (not price or price < price_min):
            continue
        if price_max and price and price > price_max:
            continue

        # Pre-compute listing URL (needed for HTML availability check)
        city = item.get("City", "")
        city_slug = city.lower().replace(" ", "-").replace("'", "")
        web_id = str(item.get("WebID", ""))
        type_label = WEBID_TO_LABEL.get(web_id, "eigendom")
        goal = item.get("Goal", 0)
        tx_path = "huren" if goal == 1 else "kopen"
        tx_slug = "te-huur" if goal == 1 else "te-koop"
        item["_listing_url"] = (
            f"https://www.heylenvastgoed.be/{tx_path}"
            f"/{type_label}-{tx_slug}-in-{city_slug}/{item['ID']}"
        )
        matched.append(item)

    # Fetch detail (image + garden) and verify HTML page concurrently
    if matched:
        detail_map = await _fetch_details_batch(session, matched)
    else:
        detail_map = {}

    results = []
    for item in matched:
        prop_id = item.get("ID")
        detail = detail_map.get(prop_id)
        if detail is None:  # sold or custom 404 page
            continue
        result = _parse_item(item, detail=detail)
        if result:
            results.append(result)

    logger.info(f"[Heylen] Found {len(results)} results (from {len(items)} fetched)")
    return results
