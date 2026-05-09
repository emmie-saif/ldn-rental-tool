"""Rightmove via saved-search RSS feeds.

Rightmove's search pages are aggressively bot-protected (especially from cloud
IPs like GitHub Actions runners). The saved-search RSS feeds are a published
mechanism that's tolerated for personal aggregator use.

Setup (one-time, manual):
  1. Create a throwaway Rightmove account.
  2. For each outcode in config.OUTCODES, build the search you want (price ≤
     £3500, beds 0-2), save it, then export the RSS URL.
  3. Paste each {outcode: rss_url} pair into config.RIGHTMOVE_RSS_FEEDS.

We then fetch each feed once per run and only attempt detail-page fetches for
new IDs (IDs we've seen before are already in state.db).
"""
from __future__ import annotations
import logging
import re
import time
from typing import Iterable, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from london_rentals import config
from london_rentals.sources.base import Listing, Source

log = logging.getLogger(__name__)

ID_RX = re.compile(r"/properties/(\d+)")


class RightmoveRSS(Source):
    name = "rightmove"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.HTTP_USER_AGENT})

    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        feed_url = config.RIGHTMOVE_RSS_FEEDS.get(outcode.upper())
        if not feed_url:
            return
        feed = feedparser.parse(feed_url)
        if feed.bozo:
            log.warning("Rightmove RSS bozo for %s: %s", outcode, feed.bozo_exception)
        for entry in feed.entries:
            link = entry.get("link", "")
            m = ID_RX.search(link)
            if not m:
                continue
            sid = m.group(1)
            listing = Listing(source=self.name, source_id=sid, url=link)
            listing.title = entry.get("title")
            listing.description = entry.get("summary")
            # Try to pull a price out of the title or summary.
            text = f"{listing.title or ''} {listing.description or ''}"
            m_price = re.search(r"£\s*([\d,]+)\s*(?:pcm|per\s*month|pw|per\s*week)", text, re.I)
            if m_price:
                amount = int(m_price.group(1).replace(",", ""))
                if "pw" in m_price.group(0).lower() or "per week" in m_price.group(0).lower():
                    amount = round(amount * 52 / 12)  # convert weekly to pcm
                listing.price_pcm = amount
            yield listing

    def fetch_detail(self, listing: Listing) -> Listing:
        # Rightmove listing pages 403 from many cloud IPs. Try once with a real
        # UA and timeout; if it fails, leave the RSS-only fields in place.
        try:
            r = self.session.get(listing.url, timeout=config.HTTP_TIMEOUT_S)
            if r.status_code != 200:
                log.info("Rightmove detail %s: HTTP %s", listing.url, r.status_code)
                return listing
        except requests.RequestException as exc:
            log.info("Rightmove detail failed for %s: %s", listing.url, exc)
            return listing
        time.sleep(0.8)
        return self._parse_detail(listing, r.text)

    @staticmethod
    def _parse_detail(listing: Listing, html: str) -> Listing:
        soup = BeautifulSoup(html, "lxml")
        # Rightmove embeds a window.PAGE_MODEL JSON blob; pull what we can with
        # regex rather than executing JS.
        m = re.search(r"\"keyFeatures\"\s*:\s*\[([^\]]*)\]", html)
        if m:
            feats = re.findall(r'"([^"]+)"', m.group(1))
            listing.structured_features = feats
        m = re.search(r"\"displayAddress\"\s*:\s*\"([^\"]+)\"", html)
        if m:
            listing.address = m.group(1)
        m = re.search(r"\"latitude\"\s*:\s*(-?[0-9.]+)", html)
        if m:
            try:
                listing.lat = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"\"longitude\"\s*:\s*(-?[0-9.]+)", html)
        if m:
            try:
                listing.lng = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"\"bedrooms\"\s*:\s*(\d+)", html)
        if m:
            listing.bedrooms = int(m.group(1))
        m = re.search(r"\"bathrooms\"\s*:\s*(\d+)", html)
        if m:
            listing.bathrooms = int(m.group(1))
        m = re.search(r"\"letAvailableDate\"\s*:\s*\"([^\"]+)\"", html)
        if m:
            listing.available_from = m.group(1)
        # Description from meta.
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            listing.description = meta["content"]
        m = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", listing.address or "")
        if m:
            listing.postcode = m.group(1).upper()
        return listing
