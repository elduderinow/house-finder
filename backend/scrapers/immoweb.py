"""
Scraper for Immoweb.be - Belgium's largest real estate platform.

Immoweb exposes a public JSON search API at:
  https://www.immoweb.be/en/search/house/for-sale?countries=BE&...
The actual data comes from their search-gateway which returns JSON when
the right headers are sent, or we parse the embedded __NEXT_DATA__ JSON.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch, rate_limited_json, get_headers

logger = logging.getLogger("house-finder.scrapers.immoweb")

DOMAIN = "immoweb.be"

# Immoweb property type mapping
PROPERTY_TYPES = {
    "house": "HOUSE",
    "apartment": "APARTMENT",
    "villa": "HOUSE",  # villa is a subtype of house on Immoweb
    "townhouse": "HOUSE",
    "studio": "APARTMENT",
    "flat": "APARTMENT",
    "loft": "APARTMENT",
}

PROPERTY_SUBTYPES = {
    "villa": "VILLA",
    "townhouse": "TOWN_HOUSE",
    "studio": "STUDIO",
    "loft": "LOFT",
    "flat": "FLAT",
}


def _build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    """Build the Immoweb search URL from criteria."""
    prop_type = PROPERTY_TYPES.get(
        (criteria.building_type or "").lower(), "HOUSE,APARTMENT"
    )

    # Base URL for search results page
    tx = "for-rent" if getattr(criteria, "transaction", "buy") == "rent" else "for-sale"
    base = f"https://www.immoweb.be/en/search/{prop_type.lower()}/{tx}"
    params = [
        "countries=BE",
        f"page={page}",
        "orderBy=relevance",
    ]

    if criteria.postcodes:
        codes = ",".join(c.strip() for c in criteria.postcodes if c.strip())
        if codes:
            params.append(f"postalCodes={codes}")

    if criteria.price_min:
        params.append(f"minPrice={criteria.price_min}")
    if criteria.price_max:
        params.append(f"maxPrice={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"minBedroomCount={criteria.bedrooms_min}")
    if criteria.bedrooms_max:
        params.append(f"maxBedroomCount={criteria.bedrooms_max}")
    if criteria.sqm_min:
        params.append(f"minSurface={criteria.sqm_min}")
    if criteria.sqm_max:
        params.append(f"maxSurface={criteria.sqm_max}")

    if criteria.garden == "yes":
        params.append("hasGarden=true")
    elif criteria.garden == "no":
        params.append("hasGarden=false")

    subtype = PROPERTY_SUBTYPES.get((criteria.building_type or "").lower())
    if subtype:
        params.append(f"propertySubtypes={subtype}")

    return f"{base}?{'&'.join(params)}"


def _build_api_url(criteria: SearchCriteria, page: int = 1) -> tuple[str, dict]:
    """Build the Immoweb search-gateway API URL."""
    prop_type = PROPERTY_TYPES.get(
        (criteria.building_type or "").lower(), "HOUSE,APARTMENT"
    )

    url = "https://search-gateway.immoweb.be/api/search"
    params = {
        "countries": "BE",
        "page": str(page),
        "pageSize": "30",
        "propertyTypes": prop_type,
        "transactionTypes": "FOR_RENT" if getattr(criteria, "transaction", "buy") == "rent" else "FOR_SALE",
        "orderBy": "relevance",
    }

    if criteria.postcodes:
        params["postalCodes"] = ",".join(c.strip() for c in criteria.postcodes)
    if criteria.price_min:
        params["minPrice"] = str(criteria.price_min)
    if criteria.price_max:
        params["maxPrice"] = str(criteria.price_max)
    if criteria.bedrooms_min:
        params["minBedroomCount"] = str(criteria.bedrooms_min)
    if criteria.sqm_min:
        params["minSurface"] = str(criteria.sqm_min)
    if criteria.sqm_max:
        params["maxSurface"] = str(criteria.sqm_max)
    if criteria.garden == "yes":
        params["hasGarden"] = "true"

    return url, params


def _parse_api_results(data: dict) -> list[PropertyResult]:
    """Parse results from the Immoweb search-gateway JSON API."""
    results = []
    items = data.get("results", [])
    for item in items:
        try:
            prop = item.get("property", {})
            trans = item.get("transaction", {})
            loc = prop.get("location", {})

            price = None
            price_text = ""
            sale = trans.get("sale", {})
            if sale:
                price = sale.get("price")
                price_text = f"\u20ac{price:,.0f}" if price else "Price on request"
            else:
                price_text = "Price on request"

            postcode = str(loc.get("postalCode", ""))
            locality = loc.get("locality", "")
            location_str = f"{postcode} {locality}".strip()

            street_name = loc.get("street", loc.get("streetName", ""))
            house_num = str(loc.get("number", loc.get("houseNumber", loc.get("streetNumber", ""))))
            street = f"{street_name} {house_num}".strip() or None

            bedrooms = prop.get("bedroomCount")
            sqm = prop.get("netHabitableSurface")

            garden_raw = prop.get("hasGarden", prop.get("garden"))
            garden = bool(garden_raw) if garden_raw is not None else None
            garden_sqm_raw = prop.get("gardenSurface", prop.get("gardenArea"))
            garden_sqm = int(garden_sqm_raw) if garden_sqm_raw else None
            title_parts = []
            ptype = prop.get("type", "")
            psubtype = prop.get("subtype", "")
            if psubtype:
                title_parts.append(psubtype.replace("_", " ").title())
            elif ptype:
                title_parts.append(ptype.replace("_", " ").title())
            if bedrooms:
                title_parts.append(f"{bedrooms} bed")
            if sqm:
                title_parts.append(f"{sqm}m\u00b2")
            if locality:
                title_parts.append(f"in {locality}")
            title = " - ".join(title_parts) if title_parts else "Property"

            img = None
            media = prop.get("images", [])
            if media:
                img = media[0] if isinstance(media[0], str) else None

            link = f"https://www.immoweb.be/en/classified/{ptype.lower()}/for-sale/{locality.lower().replace(' ', '-')}/{postcode}/{item.get('id', '')}"

            listed_date = None
            pub = (item.get("publicationDate")
                   or trans.get("publicationDate")
                   or trans.get("publishedDate")
                   or item.get("createdAt")
                   or item.get("listingDate"))
            if pub:
                try:
                    listed_date = datetime.fromisoformat(pub[:10]).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text,
                location=location_str,
                postcode=postcode,
                street=street,
                link=link,
                source="Immoweb",
                bedrooms=bedrooms,
                sqm=int(sqm) if sqm else None,
                garden=garden,
                garden_sqm=garden_sqm,
                image_url=img,
                listed_date=listed_date,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse Immoweb result: {e}")
            continue
    return results


def _parse_html_results(html: str) -> list[PropertyResult]:
    """Parse results from the Immoweb HTML search page (fallback)."""
    results = []
    soup = BeautifulSoup(html, "lxml")

    # Immoweb renders listings as article.card elements
    cards = soup.select("article.card")
    for card in cards:
        try:
            # Price: Immoweb uses a custom <iw-price> web component with JSON in :price attribute
            price = None
            price_text = "Price on request"
            iw_price = card.select_one("iw-price")
            if iw_price:
                price_json_str = iw_price.get(":price")
                if price_json_str:
                    try:
                        price_data = json.loads(price_json_str)
                        price = price_data.get("mainValue")
                        price_text = price_data.get("mainDisplayPrice") or (
                            f"\u20ac{price:,.0f}" if price else "Price on request"
                        )
                    except (json.JSONDecodeError, KeyError):
                        pass

            link_el = card.select_one("a[href*='/classified/']")
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = href if href.startswith("http") else f"https://www.immoweb.be{href}"

            # Extract property type from link URL e.g. /classified/apartment/for-sale/...
            prop_type = None
            if link:
                m = re.search(r'/classified/([^/]+)/', link)
                if m:
                    raw = m.group(1).lower()
                    if raw in ("apartment", "flat", "studio", "loft", "penthouse", "kot"):
                        prop_type = "apartment"
                    elif raw in ("house", "villa", "bungalow", "mansion", "farmhouse", "town-house", "mixed-use-building"):
                        prop_type = "house"
                    else:
                        prop_type = raw

            location_el = card.select_one(".card__information--locality, [class*='locality']")
            location = location_el.get_text(strip=True) if location_el else ""
            postcode = ""
            pc_match = re.search(r'\b(\d{4})\b', location)
            if pc_match:
                postcode = pc_match.group(1)

            title_el = card.select_one("h2, h3, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else "Property"

            img = None
            img_el = card.select_one("img[src*='immowebstatic'], img[src*='immoweb']")
            if img_el:
                img = img_el.get("src") or img_el.get("data-src")

            listed_date = None
            pub_attr = card.get("data-publication-date") or card.get("data-created-date")
            if pub_attr:
                try:
                    listed_date = datetime.fromisoformat(pub_attr[:10]).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass

            results.append(PropertyResult(
                title=title,
                price=int(price) if price else None,
                price_text=price_text,
                location=location,
                postcode=postcode,
                link=link,
                source="Immoweb",
                image_url=img,
                property_type=prop_type,
                listed_date=listed_date,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse Immoweb HTML card: {e}")
            continue

    return results


async def scrape_immoweb(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    """Scrape Immoweb for property listings matching criteria."""
    all_results: list[PropertyResult] = []

    # Strategy 1: Try the JSON API first
    for page in range(1, max_pages + 1):
        url, params = _build_api_url(criteria, page)
        data = await rate_limited_json(session, url, DOMAIN, params=params)
        if data and "results" in data:
            results = _parse_api_results(data)
            all_results.extend(results)
            total = data.get("totalItems", 0)
            if len(all_results) >= total:
                break
            continue

        # Strategy 2: Fallback to HTML scraping
        html_url = _build_search_url(criteria, page)
        html = await rate_limited_fetch(session, html_url, DOMAIN)
        if html:
            results = _parse_html_results(html)
            all_results.extend(results)
            if not results:
                break  # No more results
        else:
            logger.warning(f"[Immoweb] Failed to fetch page {page}")
            break

    logger.info(f"[Immoweb] Found {len(all_results)} results")
    return all_results
