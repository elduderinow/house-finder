"""Base scraper utilities shared across all site scrapers."""

import asyncio
import random
import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger("house-finder.scrapers")

# Known sold/unavailable status keywords (Dutch + English).
# Matched as exact full text of leaf-ish elements (status badges).
_SOLD_KEYWORDS = frozenset({
    "verkocht", "sold", "verhuurd", "rented",
    "onder compromis", "onder optie", "option genomen",
    "niet beschikbaar", "unavailable",
})

# Phrases that indicate a custom 404 / page-not-found page.
# Matched as substrings inside <title> or top-level headings.
_NOT_FOUND_PHRASES = (
    "pagina niet gevonden",
    "page not found",
    "pagina bestaat niet",
    "deze pagina bestaat niet",
    "niet gevonden",
    "not found",
    "404",
)


def is_listing_sold(soup: BeautifulSoup) -> bool:
    """
    Return True if the page has a status badge indicating sold/unavailable.

    Checks leaf-ish elements (≤3 descendant tags) whose entire text exactly
    matches a known sold keyword — e.g. <div class="status-gone">verkocht</div>.
    Avoids false positives from nav text like "Verkochte panden".
    """
    for el in soup.find_all(True):
        if len(el.find_all(True)) > 3:
            continue
        text = el.get_text(separator=" ", strip=True).lower()
        if text in _SOLD_KEYWORDS:
            return True
    return False


def is_page_not_found(soup: BeautifulSoup) -> bool:
    """
    Return True if the page is a custom 404 (HTTP 200 but page-not-found content).

    Checks the <title> and top-level headings (h1/h2) for known not-found phrases.
    """
    candidates = [soup.find("title")] + soup.find_all(["h1", "h2"])
    for el in candidates:
        if el is None:
            continue
        text = el.get_text(separator=" ", strip=True).lower()
        if any(phrase in text for phrase in _NOT_FOUND_PHRASES):
            return True
    return False


def is_listing_unavailable(soup: BeautifulSoup) -> bool:
    """Return True if the listing is sold or the page does not exist."""
    return is_listing_sold(soup) or is_page_not_found(soup)


async def _verify_one(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
) -> bool:
    """
    Fetch a single listing page and return True if it is available.
    Uses its own semaphore instead of the global rate limiter so verification
    runs concurrently without the per-domain 2 s serialisation delay.
    On any fetch error, returns True (assume available — don't silently drop).
    """
    async with sem:
        try:
            async with session.get(
                url,
                headers=get_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return False
                html = await resp.text()
                return not is_listing_unavailable(BeautifulSoup(html, "lxml"))
        except Exception:
            return True  # network error ≠ sold; keep the listing


async def filter_available_listings(
    session: aiohttp.ClientSession,
    results: list,
    concurrency: int = 6,
) -> list:
    """
    Verify every PropertyResult by fetching its listing page and drop any that
    are sold or return a custom 404. Run concurrently (default 6 at a time).

    Use this in every scraper that does NOT already fetch individual listing
    pages as part of its scraping flow (e.g. overview-only scrapers).
    Scrapers that already fetch and check detail pages (Jamar, Heylen) do not
    need this — calling it would double their requests.
    """
    if not results:
        return results

    sem = asyncio.Semaphore(concurrency)

    async def check(result):
        if not result.link:
            return result
        ok = await _verify_one(session, result.link, sem)
        return result if ok else None

    checked = await asyncio.gather(*[check(r) for r in results])
    available = [r for r in checked if r is not None]
    dropped = len(results) - len(available)
    if dropped:
        logger.info(f"filter_available_listings: dropped {dropped} unavailable listings")
    return available

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]

# Rate limiting: track last request time per domain
_last_request_time: dict[str, float] = {}
_rate_limit_lock = asyncio.Lock()
MIN_DELAY_SECONDS = 2.0  # Minimum delay between requests to same domain


def get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        "Accept-Language": "en-BE,en;q=0.9,nl;q=0.8,fr;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
    }


async def rate_limited_fetch(
    session: aiohttp.ClientSession,
    url: str,
    domain: str,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 30,
) -> Optional[str]:
    """Fetch a URL with rate limiting per domain."""
    import time

    async with _rate_limit_lock:
        now = time.time()
        last = _last_request_time.get(domain, 0)
        wait = MIN_DELAY_SECONDS - (now - last)
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0.5, 1.5))
        _last_request_time[domain] = time.time()

    req_headers = get_headers()
    if headers:
        req_headers.update(headers)

    try:
        async with session.get(
            url,
            headers=req_headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.text()
            elif resp.status == 403:
                logger.warning(f"[{domain}] Access denied (403) for {url}")
            elif resp.status == 429:
                logger.warning(f"[{domain}] Rate limited (429) for {url}")
            else:
                logger.warning(f"[{domain}] HTTP {resp.status} for {url}")
            return None
    except asyncio.TimeoutError:
        logger.warning(f"[{domain}] Timeout fetching {url}")
        return None
    except Exception as e:
        logger.warning(f"[{domain}] Error fetching {url}: {e}")
        return None


async def rate_limited_json(
    session: aiohttp.ClientSession,
    url: str,
    domain: str,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 30,
) -> Optional[dict]:
    """Fetch JSON from a URL with rate limiting per domain."""
    import time

    async with _rate_limit_lock:
        now = time.time()
        last = _last_request_time.get(domain, 0)
        wait = MIN_DELAY_SECONDS - (now - last)
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0.5, 1.5))
        _last_request_time[domain] = time.time()

    req_headers = get_headers()
    req_headers["Accept"] = "application/json"
    if headers:
        req_headers.update(headers)

    try:
        async with session.get(
            url,
            headers=req_headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            else:
                logger.warning(f"[{domain}] HTTP {resp.status} for JSON {url}")
                return None
    except asyncio.TimeoutError:
        logger.warning(f"[{domain}] Timeout fetching JSON {url}")
        return None
    except Exception as e:
        logger.warning(f"[{domain}] Error fetching JSON {url}: {e}")
        return None
