Add a new real estate source scraper to House Finder Belgium.

Follow these steps exactly. Do not skip any step.

Before starting: check `agencies.md` for the source's website URL and any notes about it.

## Step 1 — Create the scraper file

Create `backend/scrapers/<sourcename>.py`. Use lowercase, no hyphens (e.g. `realo.py`, `century21.py`).

The file must expose exactly one public async function:

```python
async def scrape_<sourcename>(
    session: aiohttp.ClientSession,
    criteria: SearchCriteria,
) -> list[PropertyResult]:
```

Set a module-level logger and DOMAIN constant:

```python
import logging
logger = logging.getLogger("house-finder.scrapers.<sourcename>")
DOMAIN = "example.be"
```

## Step 2 — Use shared utilities from base.py

Never write raw `session.get()` calls. Always use:

```python
from .base import rate_limited_fetch, rate_limited_json, get_headers, is_listing_unavailable
```

| Utility | Use for |
|---|---|
| `rate_limited_fetch(session, url, DOMAIN)` | Fetching HTML pages |
| `rate_limited_json(session, url, DOMAIN, params={})` | Fetching JSON APIs |
| `get_headers()` | Randomised browser headers — pass to any manual `session.get()` |
| `is_listing_unavailable(soup)` | Check if a listing page is sold or a custom 404 |

**Always call `is_listing_unavailable(soup)` on every HTML listing page you fetch.** Return `None` or skip if it returns `True`. This handles sold badges (class-name agnostic, matches on text content) and custom 404 pages.

## Step 3 — Apply criteria filters

Filter results using `SearchCriteria` before returning. Always respect:

```python
# Transaction type
tx = getattr(criteria, "transaction", "buy")  # "buy" or "rent"

# Postcodes
postcode_set = {str(p) for p in criteria.postcodes} if criteria.postcodes else None
if postcode_set and postcode not in postcode_set:
    continue

# Price
if criteria.price_min and price and price < criteria.price_min: continue
if criteria.price_max and price and price > criteria.price_max: continue
```

## Step 4 — Return PropertyResult objects

Every result must be a `PropertyResult` (from `backend/models.py`):

| Field | Type | Notes |
|---|---|---|
| `title` | `str` | e.g. `"Huis - 3 bed - 120m² - in Antwerpen"` |
| `price` | `int \| None` | Raw integer e.g. `350000` |
| `price_text` | `str` | `"€350,000"` or `"Price on request"` |
| `location` | `str` | `"{postcode} {city}"` |
| `postcode` | `str` | 4-digit string |
| `street` | `str \| None` | Street + number |
| `link` | `str` | Full URL to listing |
| `source` | `str` | Must match the key used in `_build_tasks` in main.py |
| `bedrooms` | `int \| None` | |
| `sqm` | `int \| None` | Living area m² |
| `garden` | `bool \| None` | |
| `garden_sqm` | `int \| None` | |
| `image_url` | `str \| None` | |
| `property_type` | `str \| None` | Normalise to `"house"` or `"apartment"` only |
| `listed_date` | `str \| None` | ISO date `"2026-03-27"` |
| `first_seen` | `str \| None` | Leave `None` — set by main.py |

## Step 5 — Register in main.py

In `backend/main.py`, add the import and register in `_build_tasks`:

```python
from .scrapers.<sourcename> import scrape_<sourcename>

# Inside _build_tasks():
"SourceLabel": scrape_<sourcename>(session, criteria),
```

## Step 5b — Verify listing availability

Every scraper must guarantee it only returns available listings. How depends on whether it fetches individual listing pages:

**If the scraper fetches each listing's detail/HTML page** (like Jamar, Heylen): call `is_listing_unavailable(soup)` on that page and skip the listing if it returns `True`. No extra step needed.

**If the scraper only parses an overview or search results page** (like ERA, Zimmo, Realo, Immoweb): call `filter_available_listings` at the end of the entry point before returning:

```python
from .base import filter_available_listings

async def scrape_<name>(session, criteria):
    ...
    all_results = await filter_available_listings(session, all_results)
    logger.info(f"[Name] Found {len(all_results)} results")
    return all_results
```

`filter_available_listings` fetches every listing URL concurrently (6 at a time) and drops any that are sold or show a custom 404. This is the only way to be certain — do not skip this step assuming the source's API pre-filters sold listings.

## Step 6 — Register in the frontend

In `frontend/src/App.jsx`:

Add to `SOURCE_COLORS`:
```js
SourceLabel: 'bg-rose-100 text-rose-800',
```

Add to the correct group in `SOURCE_GROUPS` with `available: true`:
```js
{ id: 'SourceLabel', label: 'Display Name', available: true },
```

The `id` must exactly match the key used in `_build_tasks` and `SOURCE_COLORS`.

## Step 7 — Mark as done in agencies.md

Find the agency in `agencies.md` and change its checkbox from `- [ ]` to `- [x]`.
