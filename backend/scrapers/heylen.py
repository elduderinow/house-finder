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
from .base import rate_limited_json, get_headers

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


async def _fetch_detail(session: aiohttp.ClientSession, prop_id: int) -> dict:
    """Fetch image URL, garden_sqm, and living sqm from the detail endpoint."""
    url = f"https://www.heylenvastgoed.be/api/properties/{prop_id}/detail"
    try:
        async with session.get(
            url,
            headers=get_headers(),
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
        logger.debug(f"[Heylen] Detail fetch failed for {prop_id}: {e}")
    return {}


async def _fetch_details_batch(session: aiohttp.ClientSession, prop_ids: list[int]) -> dict[int, dict]:
    """Fetch details for multiple properties concurrently."""
    results = await asyncio.gather(
        *[_fetch_detail(session, pid) for pid in prop_ids],
        return_exceptions=True,
    )
    return {
        pid: detail
        for pid, detail in zip(prop_ids, results)
        if isinstance(detail, dict)
    }


def _parse_item(item: dict, detail: Optional[dict] = None) -> Optional[PropertyResult]:
    try:
        detail = detail or {}
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
        link = f"https://www.heylenvastgoed.be/{tx_path}/{type_label}-{tx_slug}-in-{city_slug}/{prop_id}"

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
        if postcode_set and str(item.get("Zip", "")) not in postcode_set:
            continue
        price_raw = item.get("Price")
        price = int(float(str(price_raw))) if price_raw else None
        if price_min and (not price or price < price_min):
            continue
        if price_max and price and price > price_max:
            continue
        matched.append(item)

    # Fetch detail (image + garden + sqm) concurrently for matched results only
    if matched:
        prop_ids = [item["ID"] for item in matched if item.get("ID")]
        detail_map = await _fetch_details_batch(session, prop_ids)
    else:
        detail_map = {}

    results = []
    for item in matched:
        prop_id = item.get("ID")
        result = _parse_item(item, detail=detail_map.get(prop_id))
        if result:
            results.append(result)

    logger.info(f"[Heylen] Found {len(results)} results (from {len(items)} fetched)")
    return results
