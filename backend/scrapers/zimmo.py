"""
Scraper for Zimmo.be - Belgian real estate search engine.

Zimmo aggregates listings from multiple agencies. Their search uses
a JSON API that can be queried directly.
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

logger = logging.getLogger("house-finder.scrapers.zimmo")

DOMAIN = "zimmo.be"

PROPERTY_TYPE_MAP = {
    "house": "huis",
    "apartment": "appartement",
    "villa": "villa",
    "townhouse": "huis",
    "studio": "studio",
    "flat": "appartement",
}

# Postcode → Zimmo city slug (city-postcode path format)
POSTCODE_TO_CITY = {
    "2000": "antwerpen", "2018": "antwerpen", "2020": "antwerpen",
    "2030": "antwerpen", "2040": "antwerpen", "2050": "antwerpen",
    "2060": "antwerpen", "2070": "antwerpen",
    "2100": "deurne", "2110": "wijnegem",
    "2140": "borgerhout", "2150": "borsbeek",
    "2160": "wommelgem", "2170": "merksem", "2180": "ekeren",
    "2600": "berchem", "2610": "wilrijk",
    "2800": "mechelen",
    "9000": "gent", "9030": "gent", "9031": "gent",
    "9040": "gent", "9041": "gent", "9042": "gent",
    "1000": "brussel", "1050": "elsene", "1060": "sint-gillis",
    "3000": "leuven", "3500": "hasselt",
    "8000": "brugge", "8200": "brugge",
    "4000": "liege", "5000": "namur", "6000": "charleroi",
}


def _build_search_urls(criteria: SearchCriteria, page: int = 1) -> list[str]:
    """Build Zimmo search URLs — one per unique city slug."""
    prop_type = PROPERTY_TYPE_MAP.get((criteria.building_type or "").lower(), "huis")
    status = "te-huur" if getattr(criteria, "transaction", "buy") == "rent" else "te-koop"

    params = [f"type={prop_type}", f"status={status}", f"page={page}"]
    if criteria.price_min:
        params.append(f"pmin={criteria.price_min}")
    if criteria.price_max:
        params.append(f"pmax={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"slaapkamers={criteria.bedrooms_min}")
    if criteria.sqm_min:
        params.append(f"opp_min={criteria.sqm_min}")
    if criteria.sqm_max:
        params.append(f"opp_max={criteria.sqm_max}")

    qs = "&".join(params)

    if criteria.postcodes:
        seen: set[str] = set()
        urls = []
        for pc in criteria.postcodes:
            city = POSTCODE_TO_CITY.get(pc.strip())
            if city and city not in seen:
                seen.add(city)
                urls.append(f"https://www.zimmo.be/nl/{city}-{pc.strip()}/{status}/?{qs}")
        if urls:
            return urls

    return [f"https://www.zimmo.be/nl/zoeken/?{qs}"]


def _build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    return _build_search_urls(criteria, page)[0]


def _build_api_url(criteria: SearchCriteria, page: int = 1) -> tuple[str, dict]:
    """Build Zimmo API search URL."""
    prop_type = PROPERTY_TYPE_MAP.get(
        (criteria.building_type or "").lower(), "house"
    )

    url = "https://www.zimmo.be/api/v2/search"
    params: dict = {
        "type": prop_type,
        "status": "for-sale",
        "page": page,
        "limit": 24,
    }

    if criteria.postcodes:
        params["postalCodes"] = ",".join(c.strip() for c in criteria.postcodes)
    if criteria.price_min:
        params["priceMin"] = criteria.price_min
    if criteria.price_max:
        params["priceMax"] = criteria.price_max
    if criteria.bedrooms_min:
        params["bedroomsMin"] = criteria.bedrooms_min
    if criteria.sqm_min:
        params["surfaceMin"] = criteria.sqm_min
    if criteria.sqm_max:
        params["surfaceMax"] = criteria.sqm_max
    if criteria.garden == "yes":
        params["garden"] = "true"

    return url, params


def _parse_api_results(data: dict) -> list[PropertyResult]:
    """Parse Zimmo API JSON results."""
    results = []
    items = data.get("results", data.get("items", data.get("data", [])))
    if not isinstance(items, list):
        return results

    for item in items:
        try:
            price = item.get("price")
            price_text = f"\u20ac{price:,.0f}" if price else "Price on request"

            postcode = str(item.get("postalCode", item.get("zipCode", "")))
            city = item.get("city", item.get("municipality", ""))
            location = f"{postcode} {city}".strip()

            street_name = item.get("street", item.get("streetName", ""))
            house_num = str(item.get("houseNumber", item.get("number", "")))
            street = f"{street_name} {house_num}".strip() or None

            prop_type = item.get("type", item.get("propertyType", "Property"))
            bedrooms = item.get("bedrooms", item.get("bedroomCount"))
            sqm = item.get("surface", item.get("livingArea"))

            garden_raw = item.get("hasGarden", item.get("garden"))
            garden = bool(garden_raw) if garden_raw is not None else None
            garden_sqm_raw = item.get("gardenSurface", item.get("gardenArea"))
            garden_sqm = int(garden_sqm_raw) if garden_sqm_raw else None

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
                link = f"https://www.zimmo.be{slug}"
            elif slug:
                link = slug
            else:
                link = f"https://www.zimmo.be/en/detail/{item_id}"

            img = item.get("image", item.get("mainImage", item.get("thumbnail")))

            results.append(PropertyResult(
                title=title,
                price=int(price) if price else None,
                price_text=price_text,
                location=location,
                postcode=postcode,
                street=street,
                link=link,
                source="Zimmo",
                bedrooms=int(bedrooms) if bedrooms else None,
                sqm=int(sqm) if sqm else None,
                garden=garden,
                garden_sqm=garden_sqm,
                image_url=img,
            ))
        except Exception as e:
            logger.debug(f"Failed to parse Zimmo result: {e}")
            continue
    return results


def _parse_html_results(html: str) -> list[PropertyResult]:
    """Parse Zimmo HTML search results from embedded app.start() JS data."""
    results = []

    # Zimmo embeds property data in: app.start({ ..., properties: [...], ... })
    match = re.search(r'properties:\s*(\[)', html)
    if not match:
        return results

    start = match.start(1)
    depth = 0
    end = start
    for i in range(start, min(start + 2000000, len(html))):
        c = html[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        items = json.loads(html[start:end])
    except json.JSONDecodeError:
        return results

    for item in items:
        try:
            prijs = item.get("prijs", "")
            _p = int(prijs) if prijs and str(prijs).strip().isdigit() else 0
            price = _p if _p > 1000 else None
            price_text = f"\u20ac{price:,.0f}" if price else "Price on request"

            gemeente = item.get("gemeente", "")
            postcode = str(item.get("postcode", ""))
            location = f"{postcode} {gemeente}".strip()

            # "address" = combined "Binnenpad 45", straat/nummer only on detail page
            address_raw = item.get("address", item.get("adres", ""))
            street = address_raw.strip() or None

            slaapkamers = item.get("slaapkamers")
            bedrooms = int(slaapkamers) if slaapkamers else None

            tuin_raw = item.get("tuin", item.get("garden"))
            garden = bool(tuin_raw) if tuin_raw is not None else None
            tuin_opp = item.get("tuinOpp", item.get("tuinoppervlakte", item.get("b_tuinopp")))
            garden_sqm = int(float(str(tuin_opp).replace(",", "."))) if tuin_opp else None

            opp = item.get("b_woonopp", "")
            sqm = None
            if opp:
                try:
                    sqm = int(float(str(opp).replace(",", ".")))
                except (ValueError, TypeError):
                    pass

            pand_url = item.get("pand_url") or item.get("url", "")
            link = f"https://www.zimmo.be{pand_url}" if pand_url and not pand_url.startswith("http") else pand_url

            img = item.get("hoofdFoto", "")
            if not img:
                first_images = item.get("firstImages", [])
                if first_images and isinstance(first_images, list):
                    img = first_images[0] if isinstance(first_images[0], str) else None

            prop_type_raw = item.get("type", "").lower()
            if prop_type_raw in ("appartement", "studio", "flat", "loft", "penthouse"):
                prop_type = "apartment"
            elif prop_type_raw in ("huis", "villa", "bungalow", "woning", "fermette", "kasteel"):
                prop_type = "house"
            else:
                prop_type = prop_type_raw or None

            prop_type_label = item.get("type", "Huis")
            title_parts = [prop_type_label]
            if bedrooms:
                title_parts.append(f"{bedrooms} bed")
            if sqm:
                title_parts.append(f"{sqm}m\u00b2")
            if gemeente:
                title_parts.append(f"in {gemeente}")
            title = " - ".join(title_parts)

            # toegevoegd is a Unix timestamp string
            listed_date = None
            toegevoegd = item.get("toegevoegd")
            if toegevoegd:
                try:
                    listed_date = datetime.fromtimestamp(int(toegevoegd)).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text,
                location=location,
                postcode=postcode,
                street=street,
                link=link,
                source="Zimmo",
                bedrooms=bedrooms,
                sqm=sqm,
                garden=garden,
                garden_sqm=garden_sqm,
                image_url=img or None,
                property_type=prop_type,
                listed_date=listed_date,
            ))
        except Exception as e:
            logger.debug(f"Zimmo item parse error: {e}")
            continue

    return results


async def scrape_zimmo(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    """Scrape Zimmo.be for property listings."""
    all_results: list[PropertyResult] = []

    for page in range(1, max_pages + 1):
        urls = _build_search_urls(criteria, page)
        got_any = False
        for url in urls:
            html = await rate_limited_fetch(session, url, DOMAIN)
            if html:
                results = _parse_html_results(html)
                all_results.extend(results)
                if results:
                    got_any = True
            else:
                logger.warning(f"[Zimmo] Failed to fetch {url}")
        if not got_any:
            break

    logger.info(f"[Zimmo] Found {len(all_results)} results")
    return all_results
