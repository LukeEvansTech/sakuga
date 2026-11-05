"""eBay through the Browse API: an app token from client credentials, no user sign-in.

Searched on the UK marketplace, so every price comes back in pounds (converted for sellers
abroad). eBay's search for a franchise and "cel" spreads over many categories; the cels sit in
Animation Art (1529) and Other Animation Merchandise (69528), and a search takes one category,
so each search runs in each of the two (20 searches a sweep).
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
# Each franchise as "cel" and "cell"; FMA's drawings are wanted too, and their titles may not say cel.
QUERIES = [
    f"{name} {word}"
    for name in ("broly", "fullmetal alchemist", "evangelion", "pokemon")
    for word in ("cel", "cell")
] + ["fullmetal alchemist genga", "fullmetal alchemist douga"]
LIMIT = 200  # the most one page holds
MAX_PAGES = 5  # every search here fitted one page on 2026-11-05; more than 1,000 is a partial read
# Without this filter the Browse API returns Buy It Now listings only (checked 2026-11-05).
BUYING = "buyingOptions:{AUCTION|FIXED_PRICE}"


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

    def search(self, q: str, category: str, next_url: str = "") -> dict:
        """One page: the first for q in category, or the page at the previous response's next link."""
        url = next_url or f"{API}/buy/browse/v1/item_summary/search?" + urllib.parse.urlencode(
            {"q": q, "category_ids": category, "limit": LIMIT, "filter": BUYING}
        )
        if not url.startswith(f"{API}/"):
            raise ValueError("next link outside the eBay API")
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
        # v1|<listing>|<variation>: variations of one listing can be different cels.
        variation = parts[2] if len(parts) > 2 and parts[2] != "0" else ""
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
                id=f"ebay:{legacy}" + (f"-{variation}" if variation else ""),
                source="eBay",
                title=it["title"],
                url=it["itemWebUrl"] if variation else it["itemWebUrl"].split("?")[0],
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


def read(client: Client, q: str, category: str) -> Results:
    """Every page of one search; complete only when all the matches were read."""
    out, next_url = Results(), ""
    for _ in range(MAX_PAGES):
        data = client.search(q, category, next_url)
        out += parse(data)
        next_url = data.get("next") or ""
        if not next_url:  # eBay's own word that this was the last page; total is only an estimate
            out.complete = True
            return out
    out.complete = False
    return out


def jobs(client: Client) -> list:
    """(name, family, fn) per search, in the shape sources.jobs uses; fn ignores the browser page."""
    return [
        (f"ebay:{q}:{cat}", "eBay", lambda _page, q=q, cat=cat: read(client, q, cat))
        for q in QUERIES
        for cat in CATEGORIES
    ]
