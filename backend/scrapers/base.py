"""Base scraper utilities shared across all site scrapers."""

import asyncio
import random
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("house-finder.scrapers")

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
