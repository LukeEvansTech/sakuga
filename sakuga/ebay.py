"""eBay through the Browse API: an app token from client credentials, no user sign-in.

Searched on the UK marketplace, so every price comes back in pounds (converted for sellers
abroad). eBay's search for a franchise and "cel" spreads over many categories; the cels sit in
Animation Art (1529) and Other Animation Merchandise (69528), and a search takes one category,
so each franchise is searched for "cel" and "cell" in each of the two (16 searches a sweep).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request

from .sources import Raw, Results

API = "https://api.ebay.com"
CATEGORIES = ("1529", "69528")
WORDS = ("cel", "cell")
QUERIES = {
    "broly": "broly",
    "fma": "fullmetal alchemist",
    "eva": "evangelion",
    "pokemon": "pokemon",
}
LIMIT = 200  # the most one page holds; every search here is well under it (2026-11-05)


class Client:
    def __init__(self, client_id: str, secret: str, marketplace: str = "EBAY_GB"):
        self.client_id, self.secret, self.marketplace = client_id, secret, marketplace
        self._token, self._expires = "", 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._expires - 300:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        req = urllib.request.Request(
            f"{API}/identity/v1/oauth2/token",
            data=urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                }
            ).encode(),
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310: fixed eBay endpoint
            d = json.load(r)
        self._token, self._expires = d["access_token"], time.time() + int(d["expires_in"])
        return self._token

    def search(self, q: str, category: str) -> dict:
        url = f"{API}/buy/browse/v1/item_summary/search?" + urllib.parse.urlencode(
            {"q": q, "category_ids": category, "limit": LIMIT}
        )
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:  # nosec B310: fixed eBay endpoint
            return json.load(r)


def _gbp(price: dict | None) -> float | None:
    if not price or price.get("currency") != "GBP":
        return None
    try:
        return float(price["value"])
    except (KeyError, TypeError, ValueError):
        return None


def parse(data: dict) -> Results:
    """One search response as listings; complete when the page held every match."""
    out = Results()
    items = data.get("itemSummaries") or []
    for it in items:
        parts = str(it.get("itemId", "")).split("|")
        legacy = parts[1] if len(parts) > 1 else it.get("legacyItemId", "")
        if not legacy or not it.get("title") or not it.get("itemWebUrl"):
            continue
        options = it.get("buyingOptions") or []
        auction = "AUCTION" in options
        gbp = _gbp(it.get("currentBidPrice") if auction else it.get("price")) or _gbp(
            it.get("price")
        )
        where = (it.get("itemLocation") or {}).get("country", "")
        if auction:
            bids = it.get("bidCount", 0)
            info = f"Auction · {bids} bid{'' if bids == 1 else 's'}"
        else:
            info = "Buy it now" + (" · offers" if "BEST_OFFER" in options else "")
        out.append(
            Raw(
                id=f"ebay:{legacy}",
                source="eBay",
                title=it["title"],
                url=it["itemWebUrl"].split("?")[0],
                img_url=(it.get("image") or {}).get("imageUrl", ""),
                gbp=gbp,
                info=f"{info} · from {where}" if where else info,
                auction=auction,
                # Not a cel category: eBay's "cell" search brings phones and CDs, so the title must say it.
                cel_category=False,
                lang="en",
            )
        )
    out.complete = int(data.get("total", 0)) <= len(items)
    return out


def jobs(client: Client) -> list:
    """(name, family, fn) per search, in the shape sources.jobs uses; fn ignores the browser page."""
    out = []
    for q in QUERIES.values():
        for word in WORDS:
            for cat in CATEGORIES:
                query = f"{q} {word}"
                out.append(
                    (
                        f"ebay:{query}:{cat}",
                        "eBay",
                        lambda _page, query=query, cat=cat: parse(client.search(query, cat)),
                    )
                )
    return out
