# london-rentals

Daily-refreshed HTML page of London rental flats that are simultaneously:

- ≤ 10-min bike from **Castle Climbing Centre** (N4 2HA) **or** **Mile End Climbing Wall** (E3 5BE)
- ≤ 30-min bike from **Kings Cross**
- ≤ £3,500 / month

Categorized 2bed/2bath, 2bed/1bath, 1bed/1bath, studio. Sorted within each category by distance to the closer gym.

## Architecture

A daily GitHub Actions cron runs `python -m london_rentals.run`:

1. Fetches three OpenRouteService cycling **isochrones** (10-min Castle, 10-min Mile End, 30-min Kings Cross).
2. Scrapes **OpenRent**, **SpareRoom** (whole-flat), and **Rightmove** (saved-search RSS only) per outcode in `config.OUTCODES`.
3. Geocodes addresses via Nominatim (cached). Filters with three independent point-in-polygon checks: `(in_castle ∨ in_mile_end) ∧ in_kings_cross`.
4. For new survivors, fetches detail pages, extracts structured features, regex-detects bathtub presence, LLM-fills ambiguous outdoor / amenity fields (Anthropic Haiku).
5. Calls ORS Directions API (cached by lat/lng rounded to 4dp) for exact bike times to the closer gym + Kings Cross.
6. Dedups across sources via cluster key `(round(lat, 3), round(lng, 3), bedrooms, price // 50)`.
7. Renders Jinja2 → `site/index.html` and deploys to GitHub Pages.

State persists in `state.db` (SQLite), committed to a `data` branch on each run.

## Setup (one-time, manual)

1. Create a public GitHub repo for this project (recommended name: `london-rentals`).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions:** add
   - `ORS_API_KEY` — sign up at openrouteservice.org (free, 2000 directions + 500 isochrones / day)
   - `ANTHROPIC_API_KEY` — for ambiguous-feature LLM extraction (Haiku, ~£0.0002/listing)
4. Create a throwaway Rightmove account, set up a saved search per outcode in the eligible region, and paste the RSS URLs into `london_rentals/config.py` (see `RIGHTMOVE_RSS_FEEDS`). Each RSS URL looks like `https://www.rightmove.co.uk/property-to-rent/find/<area>.xml?...`.

## Local development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Dry run, single source, single outcode (no DB writes, no Pages deploy)
LIMIT_OUTCODES=N4 LIMIT_SOURCES=openrent \
  ORS_API_KEY=... ANTHROPIC_API_KEY=... \
  python -m london_rentals.run --dry-run

# Full run, narrow scope
LIMIT_OUTCODES=N4,E3 \
  ORS_API_KEY=... ANTHROPIC_API_KEY=... \
  python -m london_rentals.run
open site/index.html
```

## ToS / legal

Rightmove and OpenRent both have ToS clauses against scraping. This tool is for **personal, non-commercial use** by a single user. The Rightmove integration uses only the publicly-available RSS feed, not page scraping. The deployed page carries `<meta name="robots" content="noindex">` and is meant for the operator only — don't share the URL.

If you ever want to publish this, swap to a paid property-data API (PropertyData, Zoopla SDK partner, etc.).
