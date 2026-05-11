"""SpareRoom whole-property scraper.

The /flatshare/search.pl endpoint serves a generic search form; the actual
results live at /flatshare/ with showme_whole_property=1 to restrict to
entire-property listings (not flatshares). URL:
  https://www.spareroom.co.uk/flatshare/?search=<outcode>&showme_whole_property=1
"""
from __future__ import annotations
import logging
import re
import time
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

from london_rentals import config
from london_rentals.sources.base import Listing, Source

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.spareroom.co.uk/flatshare/"
DETAIL_URL = "https://www.spareroom.co.uk/flatshare/flatshare_detail.pl"
ID_RX = re.compile(r"flatshare_id=(\d+)")


class SpareRoom(Source):
    name = "spareroom"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(config.HTTP_BROWSER_HEADERS)

    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        params = {
            "search": outcode,
            "miles_from_max": "0",
            "showme_whole_property": "1",
            "max_rent": config.MAX_RENT_CEILING_PCM,
            "rent_period": "pcm",
        }
        try:
            r = self.session.get(SEARCH_URL, params=params, timeout=config.HTTP_TIMEOUT_S)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("SpareRoom search failed for %s: %s", outcode, exc)
            return
        time.sleep(0.5)
        ids = sorted(set(ID_RX.findall(r.text)))
        for sid in ids:
            url = f"{DETAIL_URL}?flatshare_id={sid}"
            yield Listing(source=self.name, source_id=sid, url=url)

    def fetch_detail(self, listing: Listing) -> Listing:
        try:
            r = self.session.get(listing.url, timeout=config.HTTP_TIMEOUT_S)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("SpareRoom detail failed for %s: %s", listing.url, exc)
            return listing
        time.sleep(0.5)
        return self._parse_detail(listing, r.text)

    @staticmethod
    def _parse_detail(listing: Listing, html: str) -> Listing:
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("h1")
        if title:
            listing.title = title.get_text(" ", strip=True)
        desc = soup.find(id="listing_detail_description") or soup.find(class_="detaildesc")
        if desc:
            listing.description = desc.get_text(" ", strip=True)
        # Amenity tags.
        feats = []
        for el in soup.select(".feature-list li, .key-features li, .amenities li"):
            txt = el.get_text(" ", strip=True)
            if txt:
                feats.append(txt)
        listing.structured_features = feats
        # Price.
        m = re.search(r"£\s*([\d,]+)\s*(?:pcm|per\s*month|/month)", html, re.I)
        if m:
            listing.price_pcm = int(m.group(1).replace(",", ""))
        # Beds / baths.
        m = re.search(r"(\d+)\s*bed", html, re.I)
        if m:
            listing.bedrooms = int(m.group(1))
        m = re.search(r"(\d+)\s*bath", html, re.I)
        if m:
            listing.bathrooms = int(m.group(1))
        m = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", html)
        if m:
            listing.postcode = m.group(1).upper()
        m = re.search(r"available\s*(?:from)?\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|now|today|immediately)", html, re.I)
        if m:
            listing.available_from = m.group(1)
        listing.raw = {"html_len": len(html)}
        return listing
