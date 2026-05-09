from __future__ import annotations

from london_rentals.sources.base import Listing, Source
from london_rentals.sources.openrent import OpenRent
from london_rentals.sources.spareroom import SpareRoom
from london_rentals.sources.rightmove_rss import RightmoveRSS

ALL_SOURCES: list[type[Source]] = [OpenRent, SpareRoom, RightmoveRSS]

__all__ = ["Listing", "Source", "OpenRent", "SpareRoom", "RightmoveRSS", "ALL_SOURCES"]
