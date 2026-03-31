# House Finder Belgium

FastAPI backend (port 8100) + React/Vite frontend (port 3100).

## Running

```bash
# Backend
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8100 --reload

# Frontend
cd frontend && npm run dev
```

## Structure

- `backend/scrapers/` — one file per source, all share utilities from `base.py`
- `backend/models.py` — `SearchCriteria` and `PropertyResult`
- `backend/main.py` — registers scrapers in `_build_tasks()`
- `frontend/src/App.jsx` — `SOURCE_GROUPS` and `SOURCE_COLORS` control the UI

## Sources

`agencies.md` is the canonical list of all sources to scrape. It tracks implementation status with checkboxes.

When a new scraper is added and working:
1. Find the agency in `agencies.md`
2. Change `- [ ]` to `- [x]`

When picking a new source to implement, consult `agencies.md` for unchecked entries, their website URLs, and notes about their focus area.

## Scraper commands

- `/add-scraper` — step-by-step guide for adding a new source
- `/scraper-guide` — principles, patterns and rules for managing all scrapers
