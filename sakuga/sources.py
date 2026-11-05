"""Marketplace readers. Each returns a list of raw listings, or raises when the page did not load.

All four work from headless Chromium outside Japan (checked 2026-11-05):
- Yahoo Auctions through Buyee, inside Yahoo's セル画 category (2084005356). Yahoo itself refuses UK
  and EEA visitors. The card text is Google-translated, so the Japanese title is the image's alt.
- Mercari (keyword search, newest first; no cel category, so the filter does the work). Prices
  show in the visitor's currency, GBP from the UK.
- Mandarake shop, cel category 0701. A fresh browser is sent to the splash page until it has the
  `tr_mndrk_user` cookie, which the shop's front page sets.
- Mandarake auctions, category anime_cel. Their keyword search returns nothing, so the whole
  section (about 240 lots) is read and filtered here.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from .filters import FRANCHISES, gbp_of, yen_of

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)

# One entry per item link: the largest ancestor that holds links to that item only.
CARDS_JS = r"""([sel, idre]) => {
  const re = new RegExp(idre); const out = {};
  for (const a of document.querySelectorAll(sel)) {
    if (a.closest('.slick-slide')) continue;
    const m = (a.getAttribute('href') || '').match(re); if (!m) continue;
    const id = m[1]; let n = a;
    while (n.parentElement && [...n.parentElement.querySelectorAll(sel)]
        .every(x => (x.getAttribute('href')||'').match(re)?.[1] === id)) n = n.parentElement;
    const img = n.querySelector('img');
    const text = n.innerText.replace(/\s+/g, ' ').trim();
    if (!out[id] || text.length > out[id].text.length)
      out[id] = {id, href: a.href, text, alt: img ? (img.getAttribute('alt') || '') : '',
                 img: img ? (img.currentSrc || img.src || img.getAttribute('data-src') || '') : ''};
  }
  return Object.values(out);
}"""


PAGE_CAP = 240  # both Mandarake lists show at most this many; a full page may be cut short


class Results(list):
    """A search's listings; complete is False when the page may not hold every listing."""

    complete = True


@dataclass
class Raw:
    """One listing as a site shows it."""

    id: str
    source: str
    title: str
    url: str
    img_url: str = ""
    extra: str = ""
    yen: int | None = None
    gbp: float | None = None
    info: str = ""
    auction: bool = False
    cel_category: bool = False
    meta: dict = field(default_factory=dict)


def _load(page, url: str, wait_sel: str, settle: int = 2500, required: bool = True) -> None:
    """Open a results page. With required, no result cards is a failed load, never an empty search:
    every search here returns listings, and a failure must not retire live ones."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector(wait_sel, timeout=20000)
    except Exception as e:  # pylint: disable=broad-exception-caught
        if required:
            raise RuntimeError(f"no results on {page.title()[:60]!r}") from e
    page.wait_for_timeout(settle)


def _scroll_until_stable(page, sel: str, rounds: int = 12) -> None:
    """Mercari renders 120 cards but fills in their links only once scrolled into view."""
    last = -1
    for _ in range(rounds):
        n = page.evaluate(f"() => document.querySelectorAll({sel!r}).length")
        if n == last:
            break
        last = n
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(700)


def buyee(page, q: str) -> list[Raw]:
    url = (
        f"https://buyee.jp/item/search/query/{urllib.parse.quote(q)}/category/2084005356"
        "?sort=end&order=d"
    )
    _load(page, url, "li.itemCard")
    if "Buyee" not in page.title():
        raise RuntimeError(f"not a Buyee page: {page.title()[:80]!r}")
    rows = page.evaluate("""() => [...document.querySelectorAll('li.itemCard')].map(li => {
      const a = li.querySelector('.itemCard__itemName a'); const img = li.querySelector('img');
      return {href: a && a.href, jp: img ? img.getAttribute('alt') : '', en: a ? a.innerText.trim() : '',
              img: img ? (img.getAttribute('data-src') || img.src || '') : '',
              price: (li.querySelector('.g-price')||{}).textContent || '',
              fx: (li.querySelector('.g-priceFx')||{}).textContent || '',
              info: [...li.querySelectorAll('.itemCard__infoItem')]
                .map(x => x.textContent.trim().replace(/\\s+/g,' ')).join(' · ')}})""")
    out = []
    for r in rows:
        if not r["href"]:
            continue
        m = re.search(r"/([A-Za-z0-9]+)(?:\?|$)", r["href"])  # last path segment: the Yahoo id
        info = re.sub(r"Time Remaining\s*", "", r["info"]).replace("Number of Bids", "bids")
        out.append(
            Raw(
                id="yahoo:" + (m.group(1) if m else r["href"]),
                source="Yahoo",
                title=r["jp"],
                extra=r["en"],
                url=r["href"].split("?")[0],
                img_url=r["img"],
                yen=yen_of(r["price"]),
                gbp=gbp_of(r["fx"]),
                info=info.strip(" ·"),
                auction=True,
                cel_category=True,
            )
        )
    return out


def mercari(page, q: str) -> list[Raw]:
    url = (
        f"https://jp.mercari.com/search?keyword={urllib.parse.quote(q)}"
        "&status=on_sale&sort=created_time&order=desc"
    )
    _load(page, url, 'li[data-testid="item-cell"]')
    _scroll_until_stable(page, 'li[data-testid="item-cell"] a[href*="/item/"]')
    rows = page.evaluate("""() => [...document.querySelectorAll('li[data-testid="item-cell"]')]
      .map(li => { const a = li.querySelector('a'); const img = li.querySelector('img');
        return {href: a && a.href, text: li.innerText, alt: img ? (img.getAttribute('alt') || '') : '',
                img: img ? (img.currentSrc || img.src || '') : ''}})""")
    if not any(r["href"] for r in rows):
        raise RuntimeError(f"no Mercari items read ({page.title()[:60]!r})")
    out = []
    for r in rows:
        if not r["href"]:
            continue
        parts = [p.strip() for p in r["text"].split("\n") if p.strip()]
        title = parts[-1] if parts else re.sub(r"のサムネイル$", "", r["alt"])
        out.append(
            Raw(
                id="mercari:" + r["href"].rstrip("/").split("/")[-1],
                source="Mercari",
                title=title,
                url=r["href"].split("?")[0],
                img_url=r["img"],
                yen=yen_of(r["text"]),
                gbp=gbp_of(r["text"].replace("\n", " ")),
                info="fixed price",
            )
        )
    return out


def mandarake_shop(page) -> list[Raw]:
    page.goto(
        "https://order.mandarake.co.jp/order/?lang=ja", wait_until="domcontentloaded", timeout=60000
    )
    _load(
        page,
        "https://order.mandarake.co.jp/order/listPage/list?categoryCode=0701&soldOut=1"
        "&sort=arrival&sortOrder=1&dispCount=240",
        "div.thum",
    )
    rows = page.evaluate(CARDS_JS, ["a[href*='itemCode=']", r"itemCode=(\d+)"])
    if not rows:
        raise RuntimeError(f"no Mandarake shop items (page {page.url[:80]})")
    out = Results()
    out.complete = len(rows) < PAGE_CAP
    for r in rows:
        m = re.search(r"([\d,]+)円\s*\(税込\s*([\d,]+)円\)", r["text"])
        yen = int(m.group(2).replace(",", "")) if m else yen_of(r["text"])
        out.append(
            Raw(
                id="mandarake:" + r["id"],
                source="Mandarake",
                title=r["text"],
                extra=r["alt"],
                url=f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={r['id']}",
                img_url=r["img"],
                yen=yen,
                info="shop" + (" (stock to confirm)" if "在庫確認" in r["text"] else ""),
                cel_category=True,
            )
        )
    return out


def mandarake_auction(page) -> list[Raw]:
    _load(
        page,
        "https://ekizo.mandarake.co.jp/auction/item/itemsListJa.html?category=anime_cel",
        "a[id^=goItemInfo]",
    )
    expanded = True
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.evaluate(f"setSearchOption('doSetListCount', 'listCount', {PAGE_CAP})")
        page.wait_for_selector("a[id^=goItemInfo]", timeout=20000)
        page.wait_for_timeout(2000)
    except Exception:  # pylint: disable=broad-exception-caught
        expanded = False  # the first page (about 72 lots) still reads, but is not the whole section
    rows = page.evaluate(CARDS_JS, ["a[href*='itemInfoJa.html?index=']", r"index=(\d+)"])
    if not rows:
        raise RuntimeError("no Mandarake auction lots")
    out = Results()
    out.complete = expanded and len(rows) < PAGE_CAP
    for r in rows:
        m = re.search(r"\[\d+\]\s*(.+?)\s*\|?\s*Watch", r["text"])
        left = re.search(r"(ライブ開始まで|残り)\s*([\d日時間分秒]+)", r["text"])
        out.append(
            Raw(
                id="ekizo:" + r["id"],
                source="Mandarake auction",
                title=m.group(1) if m else r["text"][:160],
                url=f"https://ekizo.mandarake.co.jp/auction/item/itemInfoJa.html?index={r['id']}",
                img_url=r["img"],
                yen=yen_of(r["text"]),
                info=left.group(0) if left else "auction",
                auction=True,
                cel_category=True,
            )
        )
    return out


def mercari_available(page, url: str) -> bool | None:
    """Whether a Mercari item is still for sale: True, False, or None when the page did not say.

    The item page's buy box reads 今すぐBuyeeで購入 while it is for sale and "See more items!" once
    sold; a removed item answers HTTP 404 with 見つかりません (all checked 2026-11-05). Anything else
    (an error, a bot check) is inconclusive, never proof of removal.
    """
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if resp is not None and resp.status == 404:
        page.wait_for_timeout(1500)
        return False if "見つかりません" in page.content() else None
    try:
        page.wait_for_selector('[data-testid="checkout-button-container"]', timeout=15000)
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    text = page.inner_text('[data-testid="checkout-button-container"]')
    if "購入" in text:
        return True
    if "See more items" in text or "売り切れ" in text:
        return False
    return None


Job = tuple[str, str, Callable]  # (name, family, fn(page) -> list[Raw])


def jobs() -> list[Job]:
    """Every search one sweep runs. The family groups a site's searches for deactivation."""
    out: list[Job] = []
    for f in FRANCHISES.values():
        for q in f["buyee"]:
            out.append((f"buyee:{q}", "Yahoo", lambda p, q=q: buyee(p, q)))
    for f in FRANCHISES.values():
        for q in f["mercari"]:
            out.append((f"mercari:{q}", "Mercari", lambda p, q=q: mercari(p, q)))
    out.append(("mandarake-shop", "Mandarake", mandarake_shop))
    out.append(("mandarake-auction", "Mandarake auction", mandarake_auction))
    return out
