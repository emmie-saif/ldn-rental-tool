from __future__ import annotations

from london_rentals.sources.base import Listing, Source
from london_rentals.sources.openrent import OpenRent
from london_rentals.sources.rightmove import Rightmove

# SpareRoom: returns mostly flatshares even with whole_property=1 — disabled.
# RightmoveRSS: superseded by Rightmove (HTML scrape of public search page).
ALL_SOURCES: list[type[Source]] = [OpenRent, Rightmove]

__all__ = ["Listing", "Source", "OpenRent", "Rightmove", "ALL_SOURCES"]
