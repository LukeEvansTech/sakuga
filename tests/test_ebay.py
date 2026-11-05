"""eBay Browse API responses as listings, and the filter on real eBay titles (2026-11-05)."""

from sakuga import ebay, sources
from sakuga.filters import classify

RESPONSE = {
    "total": 3,
    "itemSummaries": [
        {
            "itemId": "v1|900025876378|0",
            "title": "Neon Genesis Evangelion Movie Air -Shinji Misato Anime Production Cel",
            "itemWebUrl": "https://www.ebay.co.uk/itm/900025876378?hash=x",
            "image": {"imageUrl": "https://i.ebayimg.com/a.jpg"},
            "price": {"value": "2709.70", "currency": "GBP"},
            "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
            "itemLocation": {"country": "GB"},
        },
        {
            "itemId": "v1|128094489909|0",
            "title": "Dragon Ball Broly Cel Artwork / From Japan",
            "itemWebUrl": "https://www.ebay.co.uk/itm/128094489909",
            "price": {"value": "71.71", "currency": "GBP"},
            "currentBidPrice": {"value": "40.00", "currency": "GBP"},
            "bidCount": 1,
            "buyingOptions": ["AUCTION"],
            "itemLocation": {"country": "JP"},
        },
        {"itemId": "v1|1|0", "title": "", "itemWebUrl": "https://www.ebay.co.uk/itm/1"},
    ],
}


def test_parse_reads_ids_prices_and_auctions():
    got = ebay.parse(RESPONSE)
    assert got.complete
    assert [r.id for r in got] == ["ebay:900025876378", "ebay:128094489909"]
    fixed, auction = got
    assert (fixed.gbp, fixed.auction, fixed.lang, fixed.source) == (2709.7, False, "en", "eBay")
    assert fixed.url == "https://www.ebay.co.uk/itm/900025876378"
    assert fixed.info == "Buy it now · offers · from GB"
    assert (auction.gbp, auction.auction, auction.info) == (40.0, True, "Auction · 1 bid · from JP")


def test_variations_stay_distinct():
    item = {"title": "Evangelion cel", "itemWebUrl": "https://www.ebay.co.uk/itm/5?var=111"}
    got = ebay.parse(
        {
            "total": 2,
            "itemSummaries": [{**item, "itemId": "v1|5|111"}, {**item, "itemId": "v1|5|222"}],
        }
    )
    assert [r.id for r in got] == ["ebay:5-111", "ebay:5-222"]
    assert got[0].url.endswith("?var=111")


def test_a_cut_off_page_is_partial():
    assert not ebay.parse({**RESPONSE, "total": 500}).complete


def test_ebay_runs_only_with_keys():
    assert all(family != "eBay" for _, family, _ in sources.jobs())
    names = [n for n, family, _ in ebay.jobs(ebay.Client("id", "secret"))]
    assert len(names) == 20 and "ebay:fullmetal alchemist genga:1529" in names


def k(title, yen=None, auction=False):
    c = classify(title, "", yen, False, auction, english=True)
    return c and (c["franchise"], c["kind"])


def test_english_titles():
    assert k("Neon Genesis Evangelion Movie Air -Shinji Misato Anime Production Cel", 500000) == (
        "eva",
        "cel",
    )
    assert k("Fullmetal Alchemist Edward Elric production cell", 20000) == ("fma", "cel")
    assert k("Neon Genesis Evangelion Cel Club Cel Animation Shinji Rei", 14000) is None
    assert k("Evangelion Rei Ayanami Framed Animation Cel / Cel-Style Art", 500000) is None
    assert k("Pokemon Pikachu sericel limited edition", 9000) is None
    assert k("Evangelion Asuka custom hand painted cel fan art", 9000) is None
    assert k("Evangelion Neon Genesis Repro Animation Cel Animation", 20000) is None
    assert k("Sharp NTT Docomo Cell mobile Phone SH-06A Evangelion NERV Japan USED", 60000) is None
    assert k("Pokemon TV Anime Theme Songs 2019-2022 CD Cell Version CD", 5000) is None
    assert k("Neon Genesis Evangelion Cellu Club No.5: Ryoji Kaji Cel - from JAPAN", 5000) is None
    assert k("Anime Genga not Cel Evangelion #264", 200000) is None  # drawings: cels only for Eva
    assert k("Anime Douga not Cel Full Metal Alchemist #279", 15000) == ("fma", "genga")
    assert k("Anime not Cel fullmetal alchemist Settei Sheets - 77 A4 Format", 15000) is None
    assert (
        k("Neon Genesis Evangelion Animation Cel Print PUBLICITY CONCEPT anime Art Lot", 3200)
        is None
    )
    assert (
        k("Pokemon Charizard Meiji Dairies Art Panel Cel Prize Rare Complete Japan", 250000) is None
    )
    # GAINAX's numbered limited reproductions are wanted in English too; print cels are not.
    gx = "Evangelion GAINAX Limited Repro Cel Kaworu Nagisa No.04/100 with certificate"
    assert k(gx, 300000) == ("eva", "gainax-limited")
    assert k("Neon Genesis Evangelion Reproduction Animation Cel numbered 12 of 100", 90000) == (
        "eva",
        "gainax-limited",
    )
    assert (
        k("Evangelion Rei Ayanami Reproduction Cel Art Sheet 297x210mm A4 Display Japan", 30000)
        is None
    )
    assert (
        k("Evangelion Print Club Limited Cel Misato Katsuragi Anime Used [Age 13+]", 14000) is None
    )
    assert k("Evangelion Limited Print Cel Art Rei Ayanami", 16000) is None
    assert (
        k("Evangelion Limited Paint Asuka Langley SEGA Cellu Club Reproduction Cel Rare", 96000)
        is None
    )
    # Japanese titles still name the franchise only in Japanese.
    assert classify("Evangelion セル画", "", 9000, True) is None


class _Pages(ebay.Client):
    def __init__(self, total, per_page):
        super().__init__("id", "secret")
        self.total, self.per_page = total, per_page

    def search(self, q, category, next_url=""):
        offset = int(next_url.rsplit("=", 1)[1]) if next_url else 0
        n = max(0, min(self.per_page, self.total - offset))
        items = [
            {
                "itemId": f"v1|{offset + i}|0",
                "title": "Evangelion cel",
                "itemWebUrl": f"https://e/{offset + i}",
            }
            for i in range(n)
        ]
        page = {"total": self.total, "itemSummaries": items}
        if offset + n < self.total:
            page["next"] = f"{ebay.API}/buy/browse/v1/item_summary/search?offset={offset + n}"
        return page


def test_read_follows_next_links_and_marks_cut_off_reads():
    got = ebay.read(_Pages(450, 200), "evangelion cel", "1529")
    assert len(got) == 450 and got.complete
    big = ebay.read(_Pages(5000, 200), "pokemon cel", "1529")
    assert len(big) == 1000 and not big.complete
    assert ebay.read(_Pages(0, 200), "broly cell", "69528").complete


def test_a_next_link_elsewhere_is_refused():
    client = ebay.Client("id", "secret")
    client._get_token = lambda: "t"  # pylint: disable=protected-access
    try:
        client.search("q", "1529", "https://example.invalid/steal")
    except ValueError:
        return
    raise AssertionError("followed a next link off the eBay API")
