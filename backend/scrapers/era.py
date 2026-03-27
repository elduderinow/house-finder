"""
Scraper for ERA Belgium (era.be) - major real estate franchise network.
ERA uses a Drupal CMS with city-based URLs: /nl/te-koop/{city}
"""

import logging
import re

import aiohttp
from bs4 import BeautifulSoup

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch

logger = logging.getLogger("house-finder.scrapers.era")

DOMAIN = "era.be"

# Belgian postcode → ERA city slug mapping
POSTCODE_TO_CITY = {
    # Antwerp city and districts
    "2000": "antwerpen", "2018": "antwerpen", "2020": "antwerpen",
    "2030": "antwerpen", "2040": "antwerpen", "2050": "antwerpen",
    "2060": "antwerpen", "2070": "antwerpen",
    "2100": "deurne", "2110": "wijnegem",
    "2140": "borgerhout", "2150": "borsbeek",
    "2160": "wommelgem", "2170": "merksem", "2180": "ekeren",
    "2600": "berchem", "2610": "wilrijk", "2020": "antwerpen",
    # Ghent
    "9000": "gent", "9030": "gent", "9031": "gent", "9032": "gent",
    "9040": "gent", "9041": "gent", "9042": "gent", "9050": "gent",
    "9051": "gent", "9052": "gent",
    # Brussels
    "1000": "brussel", "1020": "brussel", "1030": "schaarbeek",
    "1040": "etterbeek", "1050": "elsene", "1060": "sint-gillis",
    "1070": "anderlecht", "1080": "molenbeek-saint-jean",
    "1081": "koekelberg", "1082": "berchem-sainte-agathe",
    "1083": "ganshoren", "1090": "jette", "1120": "neder-over-heembeek",
    "1130": "haren", "1140": "evere", "1150": "woluwe-saint-pierre",
    "1160": "auderghem", "1170": "watermaal-bosvoorde",
    "1180": "ukkel", "1190": "vorst", "1200": "sint-lambrechts-woluwe",
    "1210": "sint-joost-ten-node",
    # Liège
    "4000": "liege", "4020": "liege", "4030": "liege",
    # Charleroi
    "6000": "charleroi",
    # Namur
    "5000": "namur",
    # Bruges
    "8000": "brugge", "8200": "brugge",
    # Leuven
    "3000": "leuven",
    # Hasselt
    "3500": "hasselt",
    # Mechelen
    "2800": "mechelen",
    # Aalst
    "9300": "aalst",
    # Kortrijk
    "8500": "kortrijk",
}

PROPERTY_TYPE_MAP = {
    "house": "huis",
    "apartment": "appartement",
    "villa": "huis",
    "townhouse": "huis",
    "studio": "appartement",
    "flat": "appartement",
}


def _get_cities(criteria: SearchCriteria) -> list[str]:
    """Map postcodes to unique ERA city slugs. Falls back to major cities if unknown."""
    if not criteria.postcodes:
        return ["antwerpen", "gent", "brussel"]
    cities = []
    seen = set()
    for pc in criteria.postcodes:
        city = POSTCODE_TO_CITY.get(pc.strip())
        if city and city not in seen:
            cities.append(city)
            seen.add(city)
    return cities or ["antwerpen"]


def _build_url(city: str, criteria: SearchCriteria, page: int = 1) -> str:
    tx = "te-huur" if getattr(criteria, "transaction", "buy") == "rent" else "te-koop"
    params = [f"page={page}"]
    prop_type = PROPERTY_TYPE_MAP.get((criteria.building_type or "").lower())
    if prop_type:
        params.append(f"type={prop_type}")
    if criteria.price_min:
        params.append(f"minPrice={criteria.price_min}")
    if criteria.price_max:
        params.append(f"maxPrice={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"minBedrooms={criteria.bedrooms_min}")
    if criteria.sqm_min:
        params.append(f"minLivingArea={criteria.sqm_min}")
    return f"https://www.era.be/nl/{tx}/{city}?{'&'.join(params)}"


def _parse_price(text: str) -> tuple[int | None, str]:
    """Parse ERA price string like '€ 460 000' or '€ 1 250 000'."""
    text = text.strip()
    digits = re.sub(r'[^\d]', '', text)
    if digits and len(digits) >= 4:
        price = int(digits)
        return price, f"\u20ac{price:,.0f}"
    return None, "Price on request"


def _parse_html(html: str) -> list[PropertyResult]:
    results = []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("article[about].node--property")

    for card in cards:
        try:
            about = card.get("about", "")
            link = f"https://www.era.be{about}" if about else ""

            # Property type from URL path
            prop_type = None
            if "/huis/" in about or "/woning/" in about:
                prop_type = "house"
            elif "/appartement/" in about or "/studio/" in about:
                prop_type = "apartment"

            price_el = card.select_one(".field--price")
            price, price_text = _parse_price(price_el.get_text() if price_el else "")

            # ERA address format: "Heirstraat 89, 9880 Lotenhulle"
            loc_el = card.select_one(".field--address")
            location = loc_el.get_text(strip=True) if loc_el else ""
            postcode = ""
            pc_match = re.search(r'\b(\d{4})\b', location)
            if pc_match:
                postcode = pc_match.group(1)
            # Street is everything before the postcode
            street = None
            if pc_match:
                street_part = location[:pc_match.start()].strip().rstrip(",").strip()
                if street_part:
                    street = street_part

            # Title from card text (h3 or first heading)
            title_el = card.select_one("h3, h2, .node__title, [class*=title]")
            title = title_el.get_text(strip=True) if title_el else "Property"
            if not title or title == "Property":
                # Build from card text
                card_text = card.get_text(separator=" ", strip=True)
                # ERA cards have format "... | title | price | address | ..."
                parts = [p.strip() for p in card_text.split("|") if p.strip()]
                for part in parts:
                    if len(part) > 10 and not re.search(r'€|\d{4}|\d+ m²|slpkr|bekijken|Toon', part):
                        title = part
                        break

            # Bedrooms: "2 slpkr." or "2 slaapkamer(s)" — from .field--bedrooms or card text
            bed_el = card.select_one(".field--bedrooms, [class*='bedroom']")
            bed_text = bed_el.get_text() if bed_el else card_text
            bed_match = re.search(r'(\d+)(?:\s*-\s*\d+)?\s*(?:slpkr|slaapkamer)', bed_text, re.IGNORECASE)
            bedrooms = int(bed_match.group(1)) if bed_match else None

            card_text = card.get_text()

            # Living area: "90 m² woonoppervlakte" or ".field--habitable-space"
            sqm_el = card.select_one(".field--habitable-space, [class*='habitable']")
            sqm = None
            sqm_text = sqm_el.get_text() if sqm_el else card_text
            sqm_match = re.search(r'(\d+(?:[.,]\d+)?)\s*m²?\s*woon', sqm_text)
            if sqm_match:
                sqm = int(float(sqm_match.group(1).replace(",", ".")))

            # Ground/plot area: "595 m² grondoppervlakte" (.field--ground-share)
            # No garden boolean in ERA — infer from grondopp only if explicitly labelled tuin
            garden_sqm_match = re.search(r'(\d+)\s*m²?\s*(?:tuin|tuinoppervlakte)', card_text, re.IGNORECASE)
            garden_sqm = int(garden_sqm_match.group(1)) if garden_sqm_match else None
            garden = True if garden_sqm else None

            # Image (relative URL)
            img = None
            img_el = card.select_one("img[src]")
            if img_el:
                src = img_el.get("src", "")
                img = f"https://www.era.be{src}" if src.startswith("/") else src

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text,
                location=location,
                postcode=postcode,
                street=street,
                link=link,
                source="ERA",
                bedrooms=bedrooms,
                sqm=sqm,
                garden=garden,
                garden_sqm=garden_sqm,
                image_url=img,
                property_type=prop_type,
            ))
        except Exception as e:
            logger.debug(f"ERA card parse error: {e}")
            continue

    return results


async def scrape_era(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    all_results: list[PropertyResult] = []
    cities = _get_cities(criteria)

    for city in cities:
        for page in range(1, max_pages + 1):
            url = _build_url(city, criteria, page)
            html = await rate_limited_fetch(session, url, DOMAIN)
            if html:
                results = _parse_html(html)
                all_results.extend(results)
                if not results:
                    break
            else:
                logger.warning(f"[ERA] Failed to fetch {city} page {page}")
                break

    logger.info(f"[ERA] Found {len(all_results)} results")
    return all_results
