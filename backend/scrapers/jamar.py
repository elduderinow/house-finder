"""
Scraper for Jamar Immo (jamar.immo) - Antwerp-based Belgian real estate agency.

The overview page loads all listings in a single HTML page with client-side filtering.
Strategy:
  1. Fetch overview → parse all cards → filter by postcode/price/type
  2. Batch fetch detail pages for matched listings
  3. Detail pages have: full address, Bewoonbare opp., Slaapkamers, Tuin (Ja/Nee)
"""

import asyncio
import logging
import re
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch

logger = logging.getLogger("house-finder.scrapers.jamar")

DOMAIN = "jamar.immo"

PROPERTY_TYPE_MAP = {
    "huis": "house",
    "villa": "house",
    "woning": "house",
    "bel-etage": "house",
    "fermette": "house",
    "appartement": "apartment",
    "studio": "apartment",
    "flat": "apartment",
    "penthouse": "apartment",
    "loft": "apartment",
}


def _parse_overview(html: str, criteria: SearchCriteria) -> list[dict]:
    """Parse all grid-item cards, apply postcode/price/type filters, return stubs."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.grid-item")

    postcode_set = {str(p).strip() for p in criteria.postcodes} if criteria.postcodes else None
    price_min = criteria.price_min
    price_max = criteria.price_max
    target_type = None
    if criteria.building_type:
        t = criteria.building_type.lower()
        if t in ("villa", "townhouse"):
            t = "house"
        target_type = t

    matched = []
    for card in cards:
        try:
            link_el = card.select_one("a[href]")
            if not link_el:
                continue
            href = link_el.get("href", "").strip()
            if not href.startswith("http"):
                href = f"https://jamar.immo{href}"

            # Price from data-f-p attribute (already an integer string)
            price_raw = card.get("data-f-p", "").strip()
            price = int(price_raw) if price_raw.isdigit() else None

            if price_min and price and price < price_min:
                continue
            if price_max and price and price > price_max:
                continue

            # <h4>: "2970 Schilde - huis - 5 slpk"
            h4 = card.select_one("h4")
            h4_text = h4.get_text(strip=True) if h4 else ""
            parts = [p.strip() for p in h4_text.split(" - ")]

            postcode, city = "", ""
            if parts:
                pc_match = re.match(r'^(\d{4})\s+(.+)', parts[0])
                if pc_match:
                    postcode = pc_match.group(1)
                    city = pc_match.group(2)

            if postcode_set and postcode not in postcode_set:
                continue

            prop_type_raw = parts[1].lower() if len(parts) > 1 else ""
            prop_type = PROPERTY_TYPE_MAP.get(prop_type_raw)

            if target_type and prop_type and prop_type != target_type:
                continue

            bedrooms = None
            if len(parts) > 2:
                bed_match = re.search(r'(\d+)\s*slpk', parts[2])
                if bed_match:
                    bedrooms = int(bed_match.group(1))

            h3 = card.select_one("h3")
            title = h3.get_text(strip=True) if h3 else f"{prop_type_raw.title()} in {city}"

            # Image: data-src="url_normal|url_retina" — take first part
            image_url = None
            img_el = card.select_one("[data-src]")
            if img_el:
                data_src = img_el.get("data-src", "")
                image_url = data_src.split("|")[0].strip() or None

            matched.append({
                "link": href,
                "price": price,
                "price_text": f"€{price:,.0f}" if price else "Price on request",
                "postcode": postcode,
                "city": city,
                "location": f"{postcode} {city}".strip(),
                "prop_type_raw": prop_type_raw,
                "property_type": prop_type,
                "bedrooms": bedrooms,
                "title": title,
                "image_url": image_url,
            })
        except Exception as e:
            logger.debug(f"[Jamar] Card parse error: {e}")

    return matched


def _parse_detail(html: str, stub: dict) -> Optional[PropertyResult]:
    """Parse a detail page and merge with stub from overview."""
    try:
        soup = BeautifulSoup(html, "lxml")

        # Address from <h2><a href="...maps...">Den Haaglaan 121, 2660 Hoboken</a></h2>
        street = None
        h2 = soup.select_one("h2")
        if h2:
            addr_el = h2.select_one("a") or h2
            addr_text = addr_el.get_text(strip=True)
            pc_match = re.search(r'\b(\d{4})\b', addr_text)
            if pc_match:
                street_part = addr_text[:pc_match.start()].strip().rstrip(",").strip()
                if street_part:
                    street = street_part
                stub["postcode"] = pc_match.group(1)
                city_part = addr_text[pc_match.end():].strip()
                if city_part:
                    stub["city"] = city_part
                    stub["location"] = f"{stub['postcode']} {stub['city']}".strip()

        # Specs table: <ul class="table cf"><li><span>Key</span><span>Value</span></li>
        specs = {}
        table = soup.select_one("ul.table")
        if table:
            for li in table.select("li"):
                spans = li.select("span")
                if len(spans) >= 2:
                    key = spans[0].get_text(strip=True).lower()
                    val = spans[1].get_text(strip=True)
                    specs[key] = val

        bedrooms = stub.get("bedrooms")
        if "slaapkamers" in specs:
            try:
                bedrooms = int(specs["slaapkamers"])
            except ValueError:
                pass

        sqm = None
        for key in ("bewoonbare opp.", "bewoonbare oppervlakte", "bewoonbare opp", "woonoppervlakte"):
            if key in specs:
                try:
                    sqm = int(float(specs[key].replace(",", ".")))
                    break
                except ValueError:
                    pass

        garden = None
        if "tuin" in specs:
            garden = specs["tuin"].lower() in ("ja", "yes", "1")

        # Garden sqm from extended details section (tuinoppervlakte)
        garden_sqm = None
        for li in soup.select("section.section-details li, .section-details li, ul.row-characteristics li"):
            spans = li.select("span")
            if len(spans) >= 2:
                key = spans[0].get_text(strip=True).lower()
                val = spans[1].get_text(strip=True)
                if "tuinoppervlakte" in key or ("tuin" in key and "opp" in key):
                    try:
                        garden_sqm = int(float(val.replace(",", ".")))
                        garden = True
                    except ValueError:
                        pass

        # Price override from detail sidebar
        price = stub["price"]
        price_el = soup.select_one(".price h3, .price h4, .price h5")
        if price_el:
            nums = re.findall(r'\d+', price_el.get_text().replace(".", "").replace(" ", ""))
            if nums and len(nums[0]) >= 4:
                price = int(nums[0])

        prop_type = stub.get("property_type")
        if "type" in specs:
            prop_type = PROPERTY_TYPE_MAP.get(specs["type"].lower(), prop_type)

        return PropertyResult(
            title=stub["title"],
            price=price,
            price_text=f"€{price:,.0f}" if price else "Price on request",
            location=stub["location"],
            postcode=stub["postcode"],
            street=street,
            link=stub["link"],
            source="Jamar",
            bedrooms=bedrooms,
            sqm=sqm,
            garden=garden,
            garden_sqm=garden_sqm,
            image_url=stub.get("image_url"),
            property_type=prop_type,
            listed_date=None,
        )
    except Exception as e:
        logger.debug(f"[Jamar] Detail parse error for {stub.get('link')}: {e}")
        return None


async def _fetch_detail_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    url: str,
) -> Optional[str]:
    """Fetch a single detail page with concurrency limiting."""
    async with sem:
        try:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception as e:
            logger.debug(f"[Jamar] Detail fetch failed for {url}: {e}")
    return None


async def scrape_jamar(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_detail_fetches: int = 150,
) -> list[PropertyResult]:
    tx = getattr(criteria, "transaction", "buy")
    url = "https://jamar.immo/te-huur/" if tx == "rent" else "https://jamar.immo/te-koop/"

    html = await rate_limited_fetch(session, url, DOMAIN)
    if not html:
        logger.warning("[Jamar] Failed to fetch overview page")
        return []

    stubs = _parse_overview(html, criteria)
    logger.info(f"[Jamar] {len(stubs)} listings match criteria, fetching details…")

    if not stubs:
        return []

    # Cap detail fetches and use a semaphore (8 concurrent) instead of the global rate limiter
    stubs = stubs[:max_detail_fetches]
    sem = asyncio.Semaphore(8)

    detail_htmls = await asyncio.gather(
        *[_fetch_detail_page(session, sem, stub["link"]) for stub in stubs],
        return_exceptions=True,
    )

    results = []
    for stub, detail_html in zip(stubs, detail_htmls):
        if isinstance(detail_html, Exception) or not detail_html:
            results.append(PropertyResult(
                title=stub["title"],
                price=stub["price"],
                price_text=stub["price_text"],
                location=stub["location"],
                postcode=stub["postcode"],
                link=stub["link"],
                source="Jamar",
                bedrooms=stub["bedrooms"],
                image_url=stub.get("image_url"),
                property_type=stub.get("property_type"),
            ))
        else:
            result = _parse_detail(detail_html, stub)
            if result:
                results.append(result)

    logger.info(f"[Jamar] Returning {len(results)} results")
    return results
