"""Source base class. Each source subclass implements fetch_outcode + fetch_detail."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class Listing:
    source: str
    source_id: str
    url: str
    price_pcm: Optional[int] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    available_from: Optional[str] = None        # ISO date or free text
    address: Optional[str] = None
    postcode: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    title: Optional[str] = None
    description: Optional[str] = None
    structured_features: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class Source(ABC):
    name: str

    @abstractmethod
    def fetch_outcode(self, outcode: str) -> Iterable[Listing]:
        """Yield search-result-level listings for the given UK outcode."""

    def fetch_detail(self, listing: Listing) -> Listing:
        """Enrich a listing with detail-page fields. Default: passthrough."""
        return listing
