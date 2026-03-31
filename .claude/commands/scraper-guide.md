You are working on the House Finder Belgium scraper system. This guide defines the principles, patterns, and rules for managing all source scrapers. Apply these when writing, reviewing, or modifying any scraper.

---

## Core principle: all scrapers are interchangeable

Every scraper must produce `list[PropertyResult]` with the same shape. The rest of the system (caching, deduplication, filtering, UI) is completely source-agnostic. Never add source-specific logic outside the scraper file itself.

---

## Output shape — PropertyResult

Always populate as many fields as possible. Use `None` only when the data genuinely does not exist on the source, not because it was hard to extract.

```
title        — build from: type + bedrooms + sqm + city  e.g. "Huis - 3 bed - 120m² - in Antwerpen"
price        — raw int, e.g. 350000
price_text   — formatted string, e.g. "€350,000" or "Price on request"
location     — "{postcode} {city}"
postcode     — 4-digit string, always
street       — street + house number if available, e.g. "Kerkstraat 12"
link         — full URL to the listing page (what the user will open)
source       — display name matching SOURCE_COLORS in App.jsx
bedrooms     — int or None
sqm          — living area m², int or None
garden       — True / False / None
garden_sqm   — garden surface m², int or None
image_url    — first/main photo URL or None
property_type — normalised to "house" or "apartment" only
listed_date  — ISO string "YYYY-MM-DD" or None
first_seen   — leave None, set by main.py
```

**Normalising property_type:** always map source-specific labels to `"house"` or `"apartment"`. Villa, townhouse, fermette, bel-etage → `"house"`. Studio, flat, loft, penthouse → `"apartment"`. Anything unclear → `None`.

**Building title:** compose from available data in this order: type label → bedrooms → sqm → city. Join with ` - `. Never leave title blank.

**Price parsing:** Belgian sites use dots as thousands separators (`€ 350.000`). Strip dots, commas, spaces before parsing. If price is a dict, try `value`, `amount`, `mainValue`.

**Dates:** always store as `"YYYY-MM-DD"`. Parse ISO strings with `datetime.fromisoformat(s[:10])`. Convert relative dates ("3 days ago", "1 week") to absolute using `datetime.now() - timedelta(...)`.

---

## Shared utilities — always use, never bypass

All in `backend/scrapers/base.py`. Import what you need, never roll your own.

### Fetching

```python
from .base import rate_limited_fetch, rate_limited_json, get_headers
```

- `rate_limited_fetch(session, url, DOMAIN)` — fetches HTML, respects per-domain rate limiting with random delay, rotates User-Agent, returns `str | None`
- `rate_limited_json(session, url, DOMAIN, params={})` — same but returns parsed JSON `dict | None`
- `get_headers()` — randomised browser-like headers; use for any manual `session.get()` calls

Always define `DOMAIN = "example.be"` at module level and pass it to every fetch call. Rate limiting is keyed per domain.

### Availability — mandatory for every scraper

Every scraper must guarantee it only returns available listings. There are two patterns depending on whether the scraper fetches individual listing pages:

**Pattern A — scraper fetches each listing page** (Jamar, Heylen):

```python
from .base import is_listing_unavailable

if is_listing_unavailable(soup):
    return None  # skip
```

Call this on every detail/listing page HTML you parse. Also call it on overview cards to skip sold entries early. This is already the verification — do not also call `filter_available_listings`.

**Pattern B — scraper only parses a search/overview page** (ERA, Zimmo, Realo, Immoweb):

```python
from .base import filter_available_listings

all_results = await filter_available_listings(session, all_results)
```

Call this at the end of the entry point before returning. It fetches every listing URL concurrently (6 at a time), runs `is_listing_unavailable` on each page, and drops unavailable ones. This is the only reliable approach — never assume a source's search API pre-filters sold listings.

`is_listing_unavailable` detects:
- **Sold badges** (`is_listing_sold`): leaf elements (≤3 descendants) whose full text exactly matches a known sold keyword — "verkocht", "sold", "onder compromis", etc. Class-name agnostic.
- **Custom 404 pages** (`is_page_not_found`): `<title>` and `<h1>`/`<h2>` containing known not-found phrases.

To add new keywords, update `_SOLD_KEYWORDS` and `_NOT_FOUND_PHRASES` in `base.py`. Never add keyword logic inside individual scrapers.

---

## Scraping strategies — best effort, always try harder

### Prefer JSON over HTML

If the site has a JSON API (even undocumented), use it. JSON is faster, more stable, and gives cleaner data. HTML is a fallback.

To find hidden APIs: check XHR requests in DevTools, look for `__NEXT_DATA__` embedded in the page, check `/api/` paths.

### Always implement a fallback

If the JSON API fails or changes, fall back to HTML parsing. Pattern:

```python
data = await rate_limited_json(session, api_url, DOMAIN, params=params)
if data and "results" in data:
    return _parse_api_results(data)

# Fallback: HTML
html = await rate_limited_fetch(session, html_url, DOMAIN)
if html:
    return _parse_html_results(html)
return []
```

### Pagination

Always paginate until empty or until you hit `max_pages`. Break early if the page returns zero results:

```python
for page in range(1, max_pages + 1):
    results = _scrape_page(page)
    all_results.extend(results)
    if not results:
        break
```

### Detail pages

When overview cards don't have enough data (no street, no sqm, no garden), fetch detail pages. Always:
1. Cap with a semaphore to limit concurrency (8 is a safe default)
2. Skip the listing entirely if the detail fetch returns nothing (404 or fetch error)
3. Call `is_listing_unavailable` on the detail page HTML

```python
sem = asyncio.Semaphore(8)
detail_htmls = await asyncio.gather(
    *[_fetch_detail(session, sem, stub) for stub in stubs],
    return_exceptions=True,
)
for stub, html in zip(stubs, detail_htmls):
    if isinstance(html, Exception) or not html:
        continue  # skip — do not fall back to incomplete stub data
    result = _parse_detail(html, stub)
    if result:
        results.append(result)
```

### Extracting data robustly

- Try multiple key names when parsing JSON: `item.get("bedrooms") or item.get("bedroomCount") or item.get("rooms")`
- For nested structures, guard with `or {}`: `loc = item.get("location") or item.get("address") or {}`
- Extract postcodes from text with regex when not available directly: `re.search(r'\b(\d{4})\b', text)`
- For images, try `data-src` before `src` (lazy-loaded), and `srcset` as last resort
- Wrap per-item parsing in `try/except Exception` and log at debug level — one broken card should never crash the whole scrape

### Filtering criteria

Apply all relevant `SearchCriteria` filters inside the scraper. Push as many filters as possible to the source URL/params — only post-filter in Python what the source can't do.

```python
tx = getattr(criteria, "transaction", "buy")  # always respect buy/rent
postcode_set = {str(p) for p in criteria.postcodes} if criteria.postcodes else None
# Filter postcodes, price_min, price_max, bedrooms_min, sqm_min, building_type
```

---

## Structure of a scraper file

Keep concerns separated into small, testable functions:

```
DOMAIN = "..."
PROPERTY_TYPE_MAP = {...}         # source labels → "house"/"apartment"

_build_url(criteria, page) → str  # URL/params construction
_parse_overview(html, criteria) → list[dict]  # overview page → stubs
_parse_detail(html, stub) → PropertyResult | None  # detail page → result
_parse_item(item) → PropertyResult  # JSON item → result

async def scrape_<name>(session, criteria) → list[PropertyResult]  # entry point
```

The entry point does orchestration only: fetch, parse, filter, return. No parsing logic in the entry point.

---

## Error handling rules

- Never let a single listing failure crash the scraper — use `try/except` per item
- Log scraper-level failures at `WARNING`, item-level parse errors at `DEBUG`
- Return `[]` on total failure, never raise from a scraper entry point
- On a 403/429, log it and return what you have — do not retry in the same request

---

## Things that belong in base.py, not in scrapers

- New sold/unavailable keywords → `_SOLD_KEYWORDS` or `_NOT_FOUND_PHRASES`
- New HTTP utility (e.g. POST fetcher, cookie-aware session) → new function in `base.py`
- Common parsing helpers used by 2+ scrapers → `base.py`

Single-use helpers stay in the scraper file.
