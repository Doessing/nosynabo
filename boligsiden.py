"""
Boligsiden client for fetching historical sale prices of a Danish address.

Input is a `ResolvedAddress` (from resolver.py) — we reuse its `adresse_uuid`
which is the same UUID Boligsiden's public API uses.

Notes:
  * The /addresses/{uuid} endpoint does not require any auth or API key
    (verified 2026-04-20). If that ever changes, see
    ~/.config/opencode/context-server-motd.md for the known public key.
  * Responses are cached in-process with a short TTL. Sale registrations are
    public records that only grow over time; a few minutes of staleness is
    fine and saves repeated round-trips when users re-query the same address.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from cachetools import TTLCache

from resolver import ResolvedAddress

log = logging.getLogger(__name__)

BOLIGSIDEN_ADDRESS_URL = "https://api.boligsiden.dk/addresses/{uuid}"
BOLIGSIDEN_LISTING_BASE = "https://www.boligsiden.dk/adresse/{slug}"

# Translate Boligsiden's registration types to user-facing Danish.
REGISTRATION_TYPE_LABELS = {
    "normal": "Almindeligt salg",
    "family": "Familiehandel",
    "auction": "Tvangsauktion",
    "other": "Andet",
}

# Keyed by adresse_uuid. 10 minutes is plenty — registrations are append-only
# public records and this primarily absorbs rapid re-queries.
_SALES_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=2048, ttl=600)


def _fetch_address_data(uuid: str) -> dict[str, Any]:
    """Fetch and cache the full Boligsiden /addresses/{uuid} response payload.

    Returns a dict with keys:
      ``registrations``  — enriched sale registrations (newest first)
      ``is_on_market``   — True if the property is currently listed for sale
      ``boligsiden_url`` — direct link to the Boligsiden listing (or None)
    """
    cached = _SALES_CACHE.get(uuid)
    if cached is not None:
        return cached

    resp = requests.get(BOLIGSIDEN_ADDRESS_URL.format(uuid=uuid), timeout=10)
    if resp.status_code == 404:
        result: dict[str, Any] = {
            "registrations": [],
            "is_on_market": False,
            "boligsiden_url": None,
        }
        _SALES_CACHE[uuid] = result
        return result
    resp.raise_for_status()
    data = resp.json()

    regs = data.get("registrations") or []
    enriched: list[dict[str, Any]] = []
    for r in regs:
        amount = r.get("amount")
        area = r.get("area") or r.get("livingArea")
        per_m2 = round(amount / area) if amount and area else None
        enriched.append({
            "date": r.get("date"),
            "amount": amount,
            "area": area,
            "type": r.get("type"),
            "typeLabel": REGISTRATION_TYPE_LABELS.get(
                r.get("type", ""), (r.get("type") or "").capitalize() or "Ukendt"
            ),
            "perAreaPrice": per_m2,
        })
    # Sort newest first (ISO YYYY-MM-DD so lexicographic sort is correct).
    enriched.sort(key=lambda e: e.get("date") or "", reverse=True)

    is_on_market: bool = bool(data.get("isOnMarket"))
    slug: str | None = data.get("slug")
    boligsiden_url: str | None = (
        BOLIGSIDEN_LISTING_BASE.format(slug=slug) if is_on_market and slug else None
    )

    result = {
        "registrations": enriched,
        "is_on_market": is_on_market,
        "boligsiden_url": boligsiden_url,
    }
    _SALES_CACHE[uuid] = result
    return result


def get_sales_history(resolved: ResolvedAddress) -> dict[str, Any]:
    """Top-level: resolved address -> {uuid, registrations, is_on_market, boligsiden_url}."""
    data = _fetch_address_data(resolved.adresse_uuid)
    return {
        "uuid": resolved.adresse_uuid,
        **data,
    }
