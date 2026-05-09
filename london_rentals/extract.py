"""Feature extraction: bathtub, outdoor space, building amenities.

Strategy: try structured features + regex first (cheap, deterministic). Fall
back to an LLM (Haiku) only when both sources are silent or ambiguous on a
field, never as the primary signal — agent boilerplate descriptions lie.

Output schema:
  {
    "bathtub":   "yes" | "no" | "unknown",
    "outdoor":   "garden" | "balcony" | "terrace" | "roof" | "communal" | "none" | "unknown",
    "amenities": list[str]            # e.g. ["concierge", "gym", "lift", "parking"]
  }
"""
from __future__ import annotations
import json
import logging
import os
import re
from typing import Iterable, Optional

from london_rentals import config
from london_rentals.sources.base import Listing

log = logging.getLogger(__name__)

BATHTUB_POS = re.compile(r"\b(bathtub|soaking\s*tub|claw[\s-]*foot|free[\s-]*standing\s*bath)\b", re.I)
BATH_GENERIC = re.compile(r"\bbath\b", re.I)
BATHTUB_NEG = re.compile(r"\b(shower\s*only|no\s*bath|walk[\s-]*in\s*shower|wet\s*room)\b", re.I)

OUTDOOR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("garden",   re.compile(r"\b(private\s*garden|own\s*garden|rear\s*garden)\b", re.I)),
    ("garden",   re.compile(r"\bgarden\b", re.I)),
    ("balcony",  re.compile(r"\bbalcony|balconies\b", re.I)),
    ("terrace",  re.compile(r"\bterrace|patio\b", re.I)),
    ("roof",     re.compile(r"\broof[\s-]*(terrace|garden|access)\b", re.I)),
    ("communal", re.compile(r"\b(communal|shared)\s*(garden|outdoor|courtyard)\b", re.I)),
]
OUTDOOR_NEG = re.compile(r"\bno\s*outdoor\s*space|no\s*garden\b", re.I)

AMENITY_KEYWORDS: dict[str, list[str]] = {
    "concierge":  ["concierge", "porter", "doorman"],
    "gym":        ["gym", "fitness centre", "fitness center"],
    "lift":       ["lift", "elevator"],
    "parking":    ["parking", "garage", "off-street parking"],
    "bike_storage": ["bike storage", "bicycle storage", "cycle storage"],
    "pool":       ["swimming pool", "pool"],
    "ev_charging": ["ev charging", "electric vehicle charging"],
    "video_entry": ["video entry", "video intercom", "video door"],
}


def extract_features(listing: Listing, anthropic_key: Optional[str] = None) -> dict:
    """Return the normalised feature dict for a listing."""
    text = _haystack(listing)
    structured_blob = " | ".join(listing.structured_features).lower() if listing.structured_features else ""
    bathtub = _bathtub(text, structured_blob)
    outdoor = _outdoor(text, structured_blob)
    amenities = _amenities(text, structured_blob)

    needs_llm = (outdoor == "unknown") and bool(text)
    if needs_llm and anthropic_key:
        llm_out = _llm_classify(text, anthropic_key)
        if llm_out is not None:
            if outdoor == "unknown" and llm_out.get("outdoor"):
                outdoor = llm_out["outdoor"]
            for amen in llm_out.get("amenities", []):
                if amen not in amenities:
                    amenities.append(amen)

    return {"bathtub": bathtub, "outdoor": outdoor, "amenities": amenities}


def _haystack(listing: Listing) -> str:
    parts: list[str] = []
    if listing.title:
        parts.append(listing.title)
    if listing.description:
        parts.append(listing.description)
    if listing.structured_features:
        parts.extend(listing.structured_features)
    return " \n ".join(parts)


def _bathtub(text: str, structured: str) -> str:
    if BATHTUB_NEG.search(text) or BATHTUB_NEG.search(structured):
        return "no"
    if BATHTUB_POS.search(text) or BATHTUB_POS.search(structured):
        return "yes"
    if BATH_GENERIC.search(structured):
        return "yes"
    if BATH_GENERIC.search(text):
        # "bath" alone in description is weaker signal — agents often say
        # "bathroom" or "spacious bath" loosely. Mark unknown to avoid false
        # positives. Structured features are the only reliable "yes" lane.
        return "unknown"
    return "unknown"


def _outdoor(text: str, structured: str) -> str:
    if OUTDOOR_NEG.search(text):
        return "none"
    for label, pattern in OUTDOOR_PATTERNS:
        if pattern.search(structured):
            return label
    for label, pattern in OUTDOOR_PATTERNS:
        if pattern.search(text):
            return label
    return "unknown"


def _amenities(text: str, structured: str) -> list[str]:
    found: list[str] = []
    for tag, keywords in AMENITY_KEYWORDS.items():
        for kw in keywords:
            if kw in text.lower() or kw in structured:
                found.append(tag)
                break
    return found


def _llm_classify(text: str, api_key: str) -> Optional[dict]:
    """Use Haiku to disambiguate outdoor / amenities. Returns a dict or None on error."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed; skipping LLM classification")
        return None

    snippet = text[:2000]  # Haiku is cheap; keep prompt small
    prompt = (
        "From the following London rental listing text, return a JSON object with two keys:\n"
        '  "outdoor": one of "garden", "balcony", "terrace", "roof", "communal", "none", "unknown"\n'
        '  "amenities": a list using only these tags: concierge, gym, lift, parking, bike_storage, pool, ev_charging, video_entry\n'
        'Use "unknown" for outdoor only if the text genuinely does not say. Return ONLY the JSON, no prose.\n\n'
        f"---\n{snippet}\n---"
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        out = resp.content[0].text.strip()  # type: ignore[attr-defined]
        # Strip markdown fences if any.
        if out.startswith("```"):
            out = out.strip("`")
            if out.lower().startswith("json"):
                out = out[4:]
        return json.loads(out.strip())
    except Exception as exc:
        log.warning("LLM classify failed: %s", exc)
        return None


def extract_for_all(listings: Iterable[Listing]) -> dict[tuple[str, str], dict]:
    """Convenience for offline tests / fixtures: returns {(source, source_id): features}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    out: dict[tuple[str, str], dict] = {}
    for listing in listings:
        out[(listing.source, listing.source_id)] = extract_features(listing, api_key)
    return out
