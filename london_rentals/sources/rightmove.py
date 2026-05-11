"""Rightmove scraper using the public search page's embedded property JSON.

The search results page at /property-to-rent/find.html?... is publicly
accessible and embeds a `properties` array in its HTML with everything we
need: id, bedrooms, bathrooms, price, lat/lng, displayAddress,
firstVisibleDate, summary, propertyTypeFullDescription. No detail fetch
needed — fetch_detail is a no-op.

Anti-bot note: this works from a residential IP (your Mac via launchd).
From Azure / GitHub Actions IPs it'll be 403'd within a few requests.
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Iterable, Optional

import requests

from london_rentals import config
from london_rentals.sources.base import Listing, Source

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.rightmove.co.uk/property-to-rent/find.html"
PROPS_RX = re.compile(r'"properties"\s*:\s*(\[.*?\])\s*,\s*"resultCount"', re.DOTALL)
OUTCODE_RX = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b")


class Rightmove(Source):
    name = "rightmove"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(config.HTTP_BROWSER_HEADERS)

    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        loc_id = config.RIGHTMOVE_LOCATION_IDS.get(outcode.upper())
        if not loc_id:
            log.info("Rightmove: no location id for %s — skipping", outcode)
            return
        params = {
            "searchType": "RENT",
            "locationIdentifier": loc_id,
            "maxPrice": config.MAX_RENT_CEILING_PCM,
            "maxBedrooms": config.MAX_BEDROOMS,
            "radius": "0.0",
        }
        try:
            r = self.session.get(SEARCH_URL, params=params, timeout=config.HTTP_TIMEOUT_S)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Rightmove search failed for %s: %s", outcode, exc)
            return
        time.sleep(0.8)
        props = self._extract_properties(r.text)
        log.info("Rightmove/%s extracted %d properties", outcode, len(props))
        for p in props:
            yielded = self._make_listing(p)
            if yielded is not None:
                yield yielded

    def fetch_detail(self, listing: Listing) -> Listing:
        # Already have everything from the search page; nothing to do.
        return listing

    @staticmethod
    def _extract_properties(html: str) -> list[dict]:
        m = PROPS_RX.search(html)
        if not m:
            return []
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            log.warning("Rightmove JSON parse failed: %s", exc)
            return []

    def _make_listing(self, p: dict) -> Optional[Listing]:
        try:
            pid = str(p["id"])
        except (KeyError, TypeError):
            return None
        # propertyUrl is sometimes relative ("/properties/123"), sometimes absent.
        url = p.get("propertyUrl") or f"/properties/{pid}"
        if url.startswith("/"):
            url = "https://www.rightmove.co.uk" + url
        listing = Listing(source=self.name, source_id=pid, url=url)
        listing.bedrooms = _safe_int(p.get("bedrooms"))
        listing.bathrooms = _safe_int(p.get("bathrooms"))
        # Price.
        price = p.get("price") or {}
        amount = price.get("amount")
        freq = (price.get("frequency") or "").lower()
        if amount and freq == "monthly":
            listing.price_pcm = int(amount)
        elif amount and freq == "weekly":
            listing.price_pcm = round(int(amount) * 52 / 12)
        listing.address = p.get("displayAddress")
        loc = p.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            listing.lat = float(lat)
            listing.lng = float(lng)
        listing.available_from = p.get("firstVisibleDate")
        listing.title = p.get("propertyTypeFullDescription") or p.get("propertySubType")
        listing.description = p.get("summary")
        listing.raw = {"propertySubType": p.get("propertySubType")}
        if listing.address:
            m = OUTCODE_RX.search(listing.address)
            if m:
                listing.postcode = m.group(1).upper()
        return listing


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
