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
PROPERTY_LINK_RX = re.compile(r'href=["\'](/property-to-rent/[^"\']+)["\']')


class OpenRent(Source):
    name = "openrent"
    HOME_URL = "https://www.openrent.co.uk/"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(config.HTTP_BROWSER_HEADERS)
        self._warmed = False

    def _warm(self) -> None:
        """Hit the homepage once to acquire session cookies. OpenRent serves
        405 on direct search hits from cold cloud IPs."""
        if self._warmed:
            return
        try:
            self.session.get(self.HOME_URL, timeout=config.HTTP_TIMEOUT_S)
            time.sleep(1.0)
        except requests.RequestException as exc:
            log.warning("OpenRent homepage warm-up failed: %s", exc)
        self._warmed = True

    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        self._warm()
        url = SEARCH_URL.format(area=outcode.lower())
        params = {
            "term": outcode,
            # Query at the loosest cap; per-bedroom filtering happens after we
            # know how many beds each listing has.
            "prices_max": config.MAX_RENT_CEILING_PCM,
            "bedrooms_max": config.MAX_BEDROOMS,
            "isLive": "true",
        }
        headers = {"Referer": self.HOME_URL}
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=config.HTTP_TIMEOUT_S)
                r.raise_for_status()
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
                log.info("OpenRent attempt %d for %s: %s", attempt + 1, outcode, exc)
                time.sleep(1.5 * (attempt + 1))
        if last_exc is not None:
            log.warning("OpenRent search failed for %s after 3 attempts: %s", outcode, last_exc)
            return
        time.sleep(0.5)
        for link in self._extract_links(r.text):
            source_id = self._id_from_link(link)
            if source_id is None:
                continue
            full_url = f"https://www.openrent.co.uk{link}" if link.startswith("/") else link
            yield Listing(source=self.name, source_id=source_id, url=full_url)

    def fetch_detail(self, listing: Listing) -> Listing:
        self._warm()
        try:
            r = self.session.get(
                listing.url,
                headers={"Referer": self.HOME_URL},
                timeout=config.HTTP_TIMEOUT_S,
            )
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
        # OpenRent paths look like `/property-to-rent/<area>/<title-slug>/<id>`
        # where <id> is the trailing numeric segment.
        path = link.split("?", 1)[0].rstrip("/")
        last = path.rsplit("/", 1)[-1]
        return last if last.isdigit() else None

    @staticmethod
    def _parse_detail(listing: Listing, html: str) -> Listing:
        soup = BeautifulSoup(html, "lxml")

        # --- Title: "London - 1 Bed Flat, Adolphus Road, N4 - To Rent Now for £2,100.00 p/m"
        h1 = soup.find("h1")
        if h1:
            listing.title = h1.get_text(strip=True)
        page_title = soup.title.string if soup.title and soup.title.string else ""

        # --- Address / outcode: take the "<street>, <outcode>" part from the title.
        # Title comes either from <h1> or from <title>; both have the same body.
        title_body = listing.title or page_title
        m = re.search(r"(?:Studio|Bedsit|\d+\s*Bed\s+\w+)[, ]+([^,]+),\s*([A-Z]{1,2}\d[A-Z\d]?)\b", title_body, re.I)
        if m:
            listing.address = f"{m.group(1).strip()}, {m.group(2).upper()}, London"

        # --- Bedrooms / Bathrooms: OpenRent renders a row of pills under the
        # title, each as <span class="text-secondary-emphasis">N bedrooms</span>
        # (with the unit label wrapped in a nested span). Use the pill row for
        # the numeric counts.
        for span in soup.select("span.text-secondary-emphasis"):
            txt = span.get_text(" ", strip=True)
            m_b = re.match(r"(\d+)\s+bedrooms?\b", txt, re.I)
            if m_b:
                listing.bedrooms = int(m_b.group(1))
                continue
            m_b = re.match(r"(\d+)\s+bathrooms?\b", txt, re.I)
            if m_b:
                listing.bathrooms = int(m_b.group(1))
                continue
        # Title-based bedrooms fallback for older listings without a pill row.
        if listing.bedrooms is None:
            m = re.search(r"(\d+)\s*Bed\b", title_body, re.I)
            if m:
                listing.bedrooms = int(m.group(1))
        # OpenRent classifies studios under "1 bedroom" in the pill row, but
        # the title is the source of truth for property type — override to 0.
        if re.search(r"\b(Studio|Bedsit)\b", title_body, re.I):
            listing.bedrooms = 0

        # --- Lat/Lng: <... data-lat="51.568" data-lng="-0.097" ...>
        m = re.search(r'data-lat="(-?\d+\.\d+)"', html)
        if m:
            listing.lat = float(m.group(1))
        m = re.search(r'data-lng="(-?\d+\.\d+)"', html)
        if m:
            listing.lng = float(m.group(1))

        # --- Structured table: <td class="fw-medium">Label</td><td>Value</td>
        table_fields: dict[str, str] = {}
        for row in soup.select("td.fw-medium"):
            label = row.get_text(strip=True)
            sibling = row.find_next_sibling("td")
            if sibling:
                table_fields[label] = sibling.get_text(" ", strip=True)

        rent_str = table_fields.get("Rent PCM") or table_fields.get("Rent")
        if rent_str:
            m = re.search(r"([\d,]+)", rent_str)
            if m:
                listing.price_pcm = int(m.group(1).replace(",", ""))
        if listing.price_pcm is None:
            # Title fallback: "for £2,100.00 p/m"
            m = re.search(r"for\s*[£\xa3]\s*([\d,]+)", title_body)
            if m:
                listing.price_pcm = int(m.group(1).replace(",", ""))

        avail = table_fields.get("Available From")
        if avail:
            listing.available_from = avail

        furnishing = table_fields.get("Furnishing")

        # OpenRent doesn't expose bathroom count anywhere on the listing page,
        # so leave listing.bathrooms as None (the renderer will show "?").

        # --- Description
        desc_el = soup.find(id="description") or soup.find(class_="description")
        if desc_el:
            listing.description = desc_el.get_text(" ", strip=True)
        else:
            meta = soup.find("meta", attrs={"name": "twitter:description"}) \
                or soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                listing.description = meta["content"]

        # --- Postcode: derive from the parsed address rather than scanning the
        # full HTML (which would pick up OpenRent's office postcode).
        if listing.address:
            m = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", listing.address)
            if m:
                listing.postcode = m.group(1).upper()

        # --- Structured features: collect the few that OpenRent does expose.
        feats: list[str] = []
        if furnishing:
            feats.append(f"Furnishing: {furnishing}")
        for label in ("EPC Rating",):
            if label in table_fields:
                feats.append(f"{label}: {table_fields[label]}")
        listing.structured_features = feats

        listing.raw = {"html_len": len(html), "table_fields": list(table_fields.keys())}
        return listing
