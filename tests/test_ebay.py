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


def test_a_cut_off_page_is_partial():
    assert not ebay.parse({**RESPONSE, "total": 500}).complete


def test_ebay_runs_only_with_keys():
    assert all(family != "eBay" for _, family, _ in sources.jobs())
    names = [n for n, family, _ in sources.jobs(ebay.Client("id", "secret")) if family == "eBay"]
    assert len(names) == 16 and "ebay:evangelion cel:1529" in names


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
    # Japanese titles still name the franchise only in Japanese.
    assert classify("Evangelion セル画", "", 9000, True) is None
