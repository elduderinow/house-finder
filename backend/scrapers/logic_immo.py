"""
Scraper for Logic-Immo.be - now redirects to Zimmo.be/fr (acquired).
Scrapes Zimmo's French interface to cover Wallonia/Brussels francophone market.
"""

import json
import logging
import re

import aiohttp

from ..models import SearchCriteria, PropertyResult
from .base import rate_limited_fetch

logger = logging.getLogger("house-finder.scrapers.logic_immo")

DOMAIN = "zimmo.be"

PROPERTY_TYPE_MAP = {
    "house": "maison",
    "apartment": "appartement",
    "villa": "villa",
    "townhouse": "maison",
    "studio": "studio",
    "flat": "appartement",
}


def _build_search_url(criteria: SearchCriteria, page: int = 1) -> str:
    prop_type = PROPERTY_TYPE_MAP.get((criteria.building_type or "").lower(), "maison")
    params = [f"type={prop_type}", "status=a-vendre", f"page={page}"]

    if criteria.price_min:
        params.append(f"pmin={criteria.price_min}")
    if criteria.price_max:
        params.append(f"pmax={criteria.price_max}")
    if criteria.bedrooms_min:
        params.append(f"chambres={criteria.bedrooms_min}")
    if criteria.sqm_min:
        params.append(f"opp_min={criteria.sqm_min}")
    if criteria.sqm_max:
        params.append(f"opp_max={criteria.sqm_max}")

    return f"https://www.zimmo.be/fr/chercher/?{'&'.join(params)}"


def _parse_html(html: str) -> list[PropertyResult]:
    """Parse Zimmo French page — same app.start({properties:[...]}) structure."""
    results = []

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

            slaapkamers = item.get("slaapkamers")
            bedrooms = int(slaapkamers) if slaapkamers else None

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

            prop_type = item.get("type", "Maison")
            title_parts = [prop_type]
            if bedrooms:
                title_parts.append(f"{bedrooms} bed")
            if sqm:
                title_parts.append(f"{sqm}m\u00b2")
            if gemeente:
                title_parts.append(f"in {gemeente}")
            title = " - ".join(title_parts)

            results.append(PropertyResult(
                title=title,
                price=price,
                price_text=price_text,
                location=location,
                postcode=postcode,
                link=link,
                source="Logic Immo",
                bedrooms=bedrooms,
                sqm=sqm,
                image_url=img or None,
            ))
        except Exception as e:
            logger.debug(f"Logic Immo item parse error: {e}")
            continue

    return results


async def scrape_logic_immo(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
    max_pages: int = 2,
) -> list[PropertyResult]:
    # Logic-immo.be was acquired by Zimmo. The French Zimmo search requires
    # city-name URL paths which we can't construct without a postcode→city mapping.
    # Disabled until a working search URL is available.
    logger.info("[Logic Immo] Disabled (acquired by Zimmo, no working search URL)")
    return []
