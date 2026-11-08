"""The Buyee reader pages through a search and tags a category's listings with its franchise."""

from sakuga import ebay, sources, sweep
from sakuga.filters import FRANCHISES


def card(i: int) -> dict:
    return {
        "href": f"https://buyee.jp/item/yahoo/auction/x{i}?conversionType=x",
        "jp": f"エゥンゲリオン セル画 {i}",
        "en": "",
        "img": "",
        "price": "12,000 円",
        "fx": "£60.00",
        "info": "Number of Bids 3",
    }


def test_buyee_pages_until_a_short_page(monkeypatch):
    pages = {1: [card(i) for i in range(100)], 2: [card(i) for i in range(100, 142)]}
    seen = []

    def fake(_page, url):
        seen.append(url)
        return pages[int(url.rsplit("page=", 1)[1])]

    monkeypatch.setattr(sources, "_buyee_page", fake)
    got = sources.buyee(None, category="2084034853", hint="eva")
    assert len(got) == 142 and got.complete
    assert got[0].hint == "eva" and got[0].id == "yahoo:x0" and got[0].yen == 12000
    assert seen[0] == "https://buyee.jp/item/search/category/2084034853?sort=end&order=d&page=1"
    monkeypatch.setattr(sources, "_buyee_page", lambda _p, _u: [card(i) for i in range(100)])
    assert not sources.buyee(None, "悟空").complete  # five full pages: a partial read
    monkeypatch.setattr(sources, "_buyee_page", lambda _p, _u: [])
    empty = sources.buyee(None, "カウボーイビバップ")
    assert empty == [] and empty.complete  # Buyee's own no-results panel: an empty search


def test_category_jobs_and_franchise_job_names():
    names = {n for n, _, _ in sources.jobs()}
    assert {"buyee-cat:eva", "buyee-cat:kiki", "buyee-cat:mononoke", "buyee-cat:bebop"} <= names
    assert "buyee-cat:goku" not in names  # Dragon Ball's category is too big to read whole
    for key in FRANCHISES:
        assert sources.job_names(key) <= names
    assert "ebay:cowboy bebop cell:1529" in sweep.franchise_jobs("bebop")
    assert sweep.franchise_jobs("bebop") & {n for n, _, _ in ebay.jobs(ebay.Client("i", "s"))}
