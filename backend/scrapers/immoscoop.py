"""
Scraper for Immoscoop.be - Belgian real estate aggregator.

Immoscoop is a Next.js app that uses server-side rendering with
dehydrated state. We parse the embedded JSON data or fall back to HTML.
"""

import json
import logging
import re
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch, get_headers

logger = logging.getLogger("house-finder.scrapers.immoscoop")

DOMAIN = "immoscoop.be"

PROPERTY_TYPE_MAP = {
    "house": "house",
    "apartment": "apartment",
    "villa": "villa",
    "townhouse": "house",
    "studio": "apartment",
    "flat": "apartment",
}


def _build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    """Build Immoscoop search URL."""
    prop_type = PROPERTY_TYPE_MAP.get(
        (criteria.building_type or "").lower(), "house"
    )

    offset = (page - 1) * 23
    base = f"https://www.immoscoop.be/en/for-sale/{prop_type}"
    params = [
        f"limit=23",
        f"offset={offset}",
        "sort=date,DESC",
        "lang=en",
    ]

    if criteria.postcodes:
        for pc in criteria.postcodes:
            params.append(f"postalCodes[]={pc.strip()}")

    if criteria.price_min:
        params.append(f"priceMin={criteria.price_min}")
    if criteria.price_max:
        params.append(f"priceMax={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"bedroomsMin={criteria.bedrooms_min}")
    if criteria.sqm_min:
        params.append(f"surfaceMin={criteria.sqm_min}")
    if criteria.sqm_max:
        params.append(f"surfaceMax={criteria.sqm_max}")
    if criteria.garden == "yes":
        params.append("garden=true")

    return f"{base}?{'&'.join(params)}"


def _parse_next_data(html: str) -> list[PropertyResult]:
    """Parse __NEXT_DATA__ embedded in Immoscoop pages."""
    results = []
    soup = BeautifulSoup(html, "lxml")

    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return results

    try:
        data = json.loads(script.string)
        page_props = data.get("props", {}).get("pageProps", {})

        # Navigate dehydratedState for React Query cached data
        dehydrated = page_props.get("dehydratedState", {})
        queries = dehydrated.get("queries", [])

        for query in queries:
            state = query.get("state", {})
            query_data = state.get("data", {})

            items = []
            if isinstance(query_data, dict):
                items = query_data.get("results", query_data.get("items", []))
            elif isinstance(query_data, list):
                items = query_data

            for item in items:
                try:
                    price = item.get("price")
                    price_text = f"\u20ac{price:,.0f}" if price else "Price on request"

                    postcode = str(item.get("postalCode", item.get("zipCode", "")))
                    city = item.get("city", item.get("municipality", ""))
                    location = f"{postcode} {city}".strip()

                    prop_type = item.get("propertyType", item.get("type", "Property"))
                    bedrooms = item.get("bedrooms", item.get("bedroomCount"))
                    sqm = item.get("livableSurface", item.get("surface"))

                    title_parts = [prop_type.title() if prop_type else "Property"]
                    if bedrooms:
                        title_parts.append(f"{bedrooms} bed")
                    if sqm:
                        title_parts.append(f"{sqm}m\u00b2")
                    if city:
                        title_parts.append(f"in {city}")
                    title = " - ".join(title_parts)

                    slug = item.get("slug", item.get("url", ""))
                    item_id = item.get("id", "")
                    if slug and not slug.startswith("http"):
                        link = f"https://www.immoscoop.be{slug}"
                    elif slug:
                        link = slug
                    else:
                        link = f"https://www.immoscoop.be/en/detail/{item_id}"

                    img = item.get("mainImage", item.get("image", item.get("thumbnail")))

                    results.append(PropertyResult(
                        title=title,
                        price=int(price) if price else None,
                        price_text=price_text,
                        location=location,
                        postcode=postcode,
                        link=link,
                        source="Immoscoop",
                        bedrooms=int(bedrooms) if bedrooms else None,
                        sqm=int(sqm) if sqm else None,
                        image_url=img,
                    ))
                except Exception as e:
                    logger.debug(f"Failed to parse Immoscoop dehydrated item: {e}")
                    continue

    except (json.JSONDecodeError, KeyError) as e:
        logger.debug(f"Failed to parse Immoscoop __NEXT_DATA__: {e}")

    return results


def _parse_html_results(html: str) -> list[PropertyResult]:
    """Parse Immoscoop HTML results (fallback)."""
    # First try __NEXT_DATA__
    results = _parse_next_data(html)
    if results:
        return results

    soup = BeautifulSoup(html, "lxml")

    # Try to find listing cards
    cards = soup.select(
        "[class*='PropertyCard'], [class*='property-card'], "
        "[class*='listing-card'], article, [class*='result']"
    )

    for card in cards[:50]:
        try:
            title_el = card.select_one("h2, h3, [class*='title']")
            price_el = card.select_one("[class*='price']")
            link_el = card.select_one("a[href*='/en/'], a[href*='/detail/']")
            location_el = card.select_one("[class*='location'], [class*='address']")

            if not title_el and not link_el:
                continue

            title = title_el.get_text(strip=True) if title_el else "Property"
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = None
            if price_text:
                nums = re.findall(r'[\d]+', price_text.replace(".", "").replace(",", ""))
                if nums and len(nums[0]) >= 4:
                    price = int(nums[0])

            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = f"https://www.immoscoop.be{href}" if href.startswith("/") else href

            location = location_el.get_text(strip=True) if location_el else ""
            postcode = ""
            pc_match = re.search(r'\b(\d{4})\b', location)
            if pc_match:
                postcode = pc_match.group(1)

            img_el = card.select_one("img")
            img = img_el.get("src") or img_el.get("data-src") if img_el else None

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text or ("Price on request" if not price else f"\u20ac{price:,}"),
                location=location,
                postcode=postcode,
                link=link,
                source="Immoscoop",
                image_url=img,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse Immoscoop HTML card: {e}")
            continue

    return results


async def scrape_immoscoop(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    """Scrape Immoscoop.be for property listings."""
    all_results: list[PropertyResult] = []

    for page in range(1, max_pages + 1):
        url = _build_search_url(criteria, page)
        html = await rate_limited_fetch(session, url, DOMAIN)
        if html:
            results = _parse_html_results(html)
            all_results.extend(results)
            if not results:
                break
        else:
            logger.warning(f"[Immoscoop] Failed to fetch page {page}")
            break

    logger.info(f"[Immoscoop] Found {len(all_results)} results")
    return all_results
