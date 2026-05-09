"""OpenRent scraper.

Search URL pattern:
  https://www.openrent.co.uk/properties-to-rent/<area>?term=<area>&prices_max=<n>&bedrooms_max=<n>

Search results render listing cards with embedded JSON in a <script> tag and
a list of property IDs. Detail pages have JSON-LD with the structured fields.

This is intentionally tolerant of schema drift: if a field can't be parsed we
just leave it None and let downstream filtering / extraction handle it.
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

from london_rentals import config
from london_rentals.sources.base import Listing, Source

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.openrent.co.uk/properties-to-rent/{area}"
LISTING_URL = "https://www.openrent.co.uk/{slug}"
PROPERTY_ID_RX = re.compile(r"/property-to-rent/[^?\"']+-(\d+)\b")
PROPERTY_LINK_RX = re.compile(r'href=["\'](/property-to-rent/[^"\']+)["\']')


class OpenRent(Source):
    name = "openrent"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.HTTP_USER_AGENT})

    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        url = SEARCH_URL.format(area=outcode.lower())
        params = {
            "term": outcode,
            "prices_max": config.RENT_CEILING_PCM,
            "bedrooms_max": config.MAX_BEDROOMS,
            "isLive": "true",
        }
        try:
            r = self.session.get(url, params=params, timeout=config.HTTP_TIMEOUT_S)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("OpenRent search failed for %s: %s", outcode, exc)
            return
        time.sleep(0.5)
        for link in self._extract_links(r.text):
            source_id = self._id_from_link(link)
            if source_id is None:
                continue
            full_url = f"https://www.openrent.co.uk{link}" if link.startswith("/") else link
            yield Listing(source=self.name, source_id=source_id, url=full_url)

    def fetch_detail(self, listing: Listing) -> Listing:
        try:
            r = self.session.get(listing.url, timeout=config.HTTP_TIMEOUT_S)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("OpenRent detail failed for %s: %s", listing.url, exc)
            return listing
        time.sleep(0.5)
        return self._parse_detail(listing, r.text)

    @staticmethod
    def _extract_links(html: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in PROPERTY_LINK_RX.finditer(html):
            link = m.group(1)
            if link not in seen:
                seen.add(link)
                out.append(link)
        return out

    @staticmethod
    def _id_from_link(link: str) -> Optional[str]:
        m = PROPERTY_ID_RX.search(link)
        return m.group(1) if m else None

    @staticmethod
    def _parse_detail(listing: Listing, html: str) -> Listing:
        soup = BeautifulSoup(html, "lxml")
        # Try JSON-LD first.
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("@type") in {"Apartment", "House", "Residence", "Accommodation", "Product"}:
                _apply_jsonld(listing, data)
                break
        # Title + description fallback.
        title = soup.find("h1")
        if title:
            listing.title = title.get_text(strip=True)
        desc = soup.find(id="description") or soup.find(class_="description")
        if desc:
            listing.description = desc.get_text(" ", strip=True)
        # Structured features: bullet list of amenities.
        features = []
        for li in soup.select("ul.property-features li, ul.features li, .key-features li"):
            txt = li.get_text(" ", strip=True)
            if txt:
                features.append(txt)
        if features:
            listing.structured_features = features
        # Price: first £nnnn pcm.
        if listing.price_pcm is None:
            m = re.search(r"£\s*([\d,]+)\s*(?:pcm|per\s*month)", html, re.I)
            if m:
                listing.price_pcm = int(m.group(1).replace(",", ""))
        # Beds / baths.
        if listing.bedrooms is None:
            m = re.search(r"(\d+)\s*bedroom", html, re.I)
            if m:
                listing.bedrooms = int(m.group(1))
        if listing.bathrooms is None:
            m = re.search(r"(\d+)\s*bathroom", html, re.I)
            if m:
                listing.bathrooms = int(m.group(1))
        # Available from.
        m = re.search(r"available\s*(?:from)?\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|now|today)", html, re.I)
        if m:
            listing.available_from = m.group(1)
        # Postcode.
        m = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", html)
        if m:
            listing.postcode = m.group(1).upper()
        listing.raw = {"html_len": len(html)}
        return listing


def _apply_jsonld(listing: Listing, data: dict) -> None:
    addr = data.get("address")
    if isinstance(addr, dict):
        parts = [
            addr.get("streetAddress"),
            addr.get("addressLocality"),
            addr.get("postalCode"),
        ]
        listing.address = ", ".join(p for p in parts if p) or listing.address
        if addr.get("postalCode"):
            listing.postcode = addr["postalCode"].upper()
    geo = data.get("geo")
    if isinstance(geo, dict):
        try:
            listing.lat = float(geo["latitude"])
            listing.lng = float(geo["longitude"])
        except (KeyError, TypeError, ValueError):
            pass
    if "numberOfRooms" in data:
        try:
            listing.bedrooms = int(data["numberOfRooms"])
        except (TypeError, ValueError):
            pass
    if "name" in data:
        listing.title = data.get("name")
    if "description" in data:
        listing.description = data.get("description")
