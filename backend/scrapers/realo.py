"""
Scraper for Realo.be - Belgian real estate aggregator.
Uses HTML parsing with __NEXT_DATA__ / dehydrated state extraction.
"""

import json
import logging
import re
from datetime import datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch, get_headers

logger = logging.getLogger("house-finder.scrapers.realo")

DOMAIN = "realo.be"

PROPERTY_TYPE_MAP = {
    "house": "houses-and-villas",
    "apartment": "apartments-and-flats",
    "villa": "houses-and-villas",
    "townhouse": "houses-and-villas",
    "studio": "apartments-and-flats",
    "flat": "apartments-and-flats",
}


# Postcode → Realo city name mapping
POSTCODE_TO_CITY = {
    "2000": "Antwerpen", "2018": "Antwerpen", "2020": "Antwerpen",
    "2030": "Antwerpen", "2040": "Antwerpen", "2050": "Antwerpen",
    "2060": "Antwerpen", "2070": "Antwerpen",
    "2100": "Deurne", "2110": "Wijnegem",
    "2140": "Borgerhout", "2150": "Borsbeek",
    "2160": "Wommelgem", "2170": "Merksem", "2180": "Ekeren",
    "2600": "Berchem", "2610": "Wilrijk",
    "9000": "Gent", "9030": "Gent", "9031": "Gent", "9032": "Gent",
    "9040": "Gent", "9041": "Gent", "9042": "Gent",
    "1000": "Brussel", "1050": "Elsene", "1060": "Sint-Gillis",
    "3000": "Leuven", "3500": "Hasselt", "2800": "Mechelen",
    "8000": "Brugge", "4000": "Liege", "5000": "Namur",
}


def _build_search_urls(criteria: SearchCriteria, page: int = 1) -> list[str]:
    prop_slug = PROPERTY_TYPE_MAP.get((criteria.building_type or "").lower(), "houses-and-villas")
    tx = "for-rent" if getattr(criteria, "transaction", "buy") == "rent" else "for-sale"

    params = [f"page={page}"]
    if criteria.price_min:
        params.append(f"minPrice={criteria.price_min}")
    if criteria.price_max:
        params.append(f"maxPrice={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"minBedrooms={criteria.bedrooms_min}")
    if criteria.sqm_min:
        params.append(f"minLivingArea={criteria.sqm_min}")
    if criteria.sqm_max:
        params.append(f"maxLivingArea={criteria.sqm_max}")
    if criteria.garden == "yes":
        params.append("hasGarden=true")

    base = f"https://www.realo.be/en/search/{prop_slug}/{tx}"
    if criteria.postcodes:
        # Group by unique city and make one request per city
        cities = []
        seen = set()
        for pc in criteria.postcodes:
            city = POSTCODE_TO_CITY.get(pc.strip())
            if city and city not in seen:
                cities.append(city)
                seen.add(city)
        if cities:
            return [f"{base}?{'&'.join(params)}&city={city}" for city in cities]
    return [f"{base}?{'&'.join(params)}"]


def _build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    return _build_search_urls(criteria, page)[0]


def _extract_items(data: dict) -> list[dict]:
    """Recursively find property item lists in the JSON blob."""
    # Direct paths
    for path in [
        lambda d: d.get("props", {}).get("pageProps", {}).get("properties", []),
        lambda d: d.get("props", {}).get("pageProps", {}).get("listings", []),
        lambda d: d.get("props", {}).get("pageProps", {}).get("results", []),
        lambda d: d.get("props", {}).get("pageProps", {}).get("items", []),
    ]:
        try:
            items = path(data)
            if isinstance(items, list) and items:
                return items
        except Exception:
            pass

    # Dehydrated React Query state (common in Next.js)
    try:
        queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
        for q in queries:
            state_data = q.get("state", {}).get("data", {})
            for key in ["results", "listings", "properties", "items", "data"]:
                candidate = state_data.get(key, [])
                if isinstance(candidate, list) and candidate:
                    return candidate
    except Exception:
        pass

    return []


def _parse_item(item: dict) -> PropertyResult:
    price = (
        item.get("price")
        or item.get("askingPrice")
        or item.get("salePrice")
        or (item.get("transaction", {}) or {}).get("price")
    )
    if isinstance(price, dict):
        price = price.get("value") or price.get("amount")

    price_val = None
    if price:
        try:
            price_val = int(float(str(price).replace(",", "").replace(".", "").replace(" ", "")))
        except Exception:
            pass

    price_text = f"€{price_val:,.0f}" if price_val else "Price on request"

    loc = item.get("location", item.get("address", {})) or {}
    if isinstance(loc, str):
        location_str = loc
        postcode = ""
        pc_match = re.search(r'\b(\d{4})\b', loc)
        if pc_match:
            postcode = pc_match.group(1)
        street = None
    else:
        postcode = str(loc.get("postalCode", loc.get("zipCode", loc.get("zip", loc.get("postal", "")))))
        city = loc.get("city", loc.get("municipality", loc.get("locality", loc.get("name", ""))))
        location_str = f"{postcode} {city}".strip()
        street_name = loc.get("street", loc.get("streetName", ""))
        house_num = str(loc.get("number", loc.get("houseNumber", loc.get("streetNumber", ""))))
        street = f"{street_name} {house_num}".strip() or None

    prop_type = item.get("type", item.get("propertyType", item.get("category", "Property")))
    if isinstance(prop_type, dict):
        prop_type = prop_type.get("name", prop_type.get("label", "Property"))

    bedrooms = item.get("bedrooms", item.get("bedroomCount"))
    sqm = item.get("livingArea", item.get("surface", item.get("livableSurface", item.get("area"))))

    garden_raw = item.get("hasGarden", item.get("garden"))
    garden = bool(garden_raw) if garden_raw is not None else None
    garden_sqm_raw = item.get("gardenSurface", item.get("gardenArea"))
    garden_sqm = int(garden_sqm_raw) if garden_sqm_raw else None

    title_parts = [str(prop_type).title() if prop_type else "Property"]
    if bedrooms:
        title_parts.append(f"{bedrooms} bed")
    if sqm:
        title_parts.append(f"{sqm}m²")
    city_str = (loc.get("city", "") if isinstance(loc, dict) else "")
    if city_str:
        title_parts.append(f"in {city_str}")
    title = " - ".join(title_parts)

    slug = item.get("slug", item.get("url", ""))
    item_id = item.get("id", item.get("reference", ""))
    if slug and not slug.startswith("http"):
        link = f"https://www.realo.be{slug}"
    elif slug:
        link = slug
    else:
        link = f"https://www.realo.be/en/property/{item_id}" if item_id else ""

    img = (
        item.get("mainImage")
        or item.get("image")
        or item.get("thumbnail")
        or (item.get("images", [None])[0] if isinstance(item.get("images"), list) else None)
    )
    if isinstance(img, dict):
        img = img.get("url") or img.get("src") or img.get("href")

    return PropertyResult(
        title=title,
        price=price_val,
        price_text=price_text,
        location=location_str,
        postcode=postcode,
        street=street,
        link=link,
        source="Realo",
        bedrooms=int(bedrooms) if bedrooms else None,
        sqm=int(float(str(sqm))) if sqm else None,
        garden=garden,
        garden_sqm=garden_sqm,
        image_url=img,
    )


def _parse_relative_date(text: str) -> str | None:
    """Convert Realo's relative date text ('1 week', '3 days') to a YYYY-MM-DD string."""
    text = text.strip().lower()
    now = datetime.now()
    try:
        if "day" in text:
            n = int(re.search(r'\d+', text).group())
            return (now - timedelta(days=n)).strftime("%Y-%m-%d")
        if "week" in text:
            n = int(re.search(r'\d+', text).group())
            return (now - timedelta(weeks=n)).strftime("%Y-%m-%d")
        if "month" in text:
            n = int(re.search(r'\d+', text).group())
            return (now - timedelta(days=n * 30)).strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        pass
    return None


def _parse_html(html: str, transaction: str = "buy") -> list[PropertyResult]:
    results = []
    soup = BeautifulSoup(html, "lxml")

    # Realo renders listings as .component-estate-grid-item divs
    cards = soup.select(".component-estate-grid-item")
    for card in cards:
        try:
            price_el = card.select_one(".label-price")
            if not price_el:
                continue
            # Skip listings that don't match the transaction type
            is_rental = "/month" in price_el.get_text()
            if transaction == "buy" and is_rental:
                continue
            if transaction == "rent" and not is_rental:
                continue

            price_text_raw = price_el.get_text(strip=True)
            price = None
            # Belgian format: "€ 265.000" — dot is thousands separator
            nums = re.findall(r'\d+', price_text_raw.replace(".", "").replace(",", "").replace(" ", ""))
            if nums and len(nums[0]) >= 4:
                price = int(nums[0])
            price_text = f"\u20ac{price:,.0f}" if price else "Price on request"

            # Link from data-href or a.link
            href = card.get("data-href") or ""
            if not href:
                link_el = card.select_one("a.link[href]")
                href = link_el.get("href", "") if link_el else ""
            href = href.strip()
            link = f"https://www.realo.be{href}" if href.startswith("/") else href

            # Full address from .address element: "Amerikalei 100, 2000 Antwerp"
            addr_el = card.select_one(".address a, .address.truncate a, .address > a")
            addr_text = addr_el.get_text(strip=True) if addr_el else ""

            postcode = ""
            pc_match = re.search(r'\b(\d{4})\b', addr_text or href)
            if pc_match:
                postcode = pc_match.group(1)

            # Street is everything before the postcode in the address text
            street = None
            if addr_text and pc_match and pc_match.string is addr_text:
                street_part = addr_text[:pc_match.start()].strip().rstrip(",").strip()
                if street_part:
                    street = street_part

            city_match = re.search(r'\b\d{4}\b\s+(.+)', addr_text) if addr_text else None
            if city_match:
                city = city_match.group(1).strip()
            else:
                url_city = re.search(r'-\d{4}-([^/?]+)', href)
                city = url_city.group(1).replace("-", " ").title() if url_city else ""

            location = f"{postcode} {city}".strip()

            # Bedrooms: ".icn-dot.beds" or text containing "slaapkamer"
            beds_el = card.select_one(".icn-dot.beds, .beds, [class*='beds']")
            bedrooms = None
            if beds_el:
                bed_match = re.search(r'(\d+)', beds_el.get_text())
                bedrooms = int(bed_match.group(1)) if bed_match else None

            # Living area: ".icn-dot.area" — "410m²"
            area_el = card.select_one(".icn-dot.area, .area, [class*='area']")
            sqm = None
            if area_el:
                sqm_match = re.search(r'(\d+)', area_el.get_text())
                sqm = int(sqm_match.group(1)) if sqm_match else None


            # Image from data-images JSON or img tag
            img = None
            carousel = card.select_one("[data-images]")
            if carousel:
                try:
                    imgs = json.loads(carousel.get("data-images", "[]"))
                    if imgs:
                        img = imgs[0].get("srcAt2x") or imgs[0].get("src")
                except (json.JSONDecodeError, KeyError):
                    pass
            if not img:
                img_el = card.select_one("img.image-responsive[src]")
                if img_el:
                    img = img_el.get("src", "").strip() or None

            title = f"Property{' - ' + city if city else ''}"

            # Relative date from clock icon span
            listed_date = None
            clock_el = card.select_one(".icn-clock, [class*='icn-clock']")
            if clock_el:
                listed_date = _parse_relative_date(clock_el.get_text())

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text,
                location=location,
                postcode=postcode,
                street=street,
                link=link,
                source="Realo",
                bedrooms=bedrooms,
                sqm=sqm,
                image_url=img,
                listed_date=listed_date,
            ))
        except Exception as e:
            logger.debug(f"Realo HTML card parse error: {e}")
            continue

    return results


async def scrape_realo(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    all_results: list[PropertyResult] = []

    tx = getattr(criteria, "transaction", "buy")
    for page in range(1, max_pages + 1):
        urls = _build_search_urls(criteria, page)
        got_any = False
        for url in urls:
            html = await rate_limited_fetch(session, url, DOMAIN)
            if html:
                results = _parse_html(html, transaction=tx)
                all_results.extend(results)
                if results:
                    got_any = True
            else:
                logger.warning(f"[Realo] Failed to fetch {url}")
        if not got_any:
            break

    logger.info(f"[Realo] Found {len(all_results)} results")
    return all_results
