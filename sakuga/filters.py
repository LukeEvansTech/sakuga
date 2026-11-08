"""Which listings are worth cataloguing: franchise from the Japanese title, junk and prints dropped.

Ported from the celwatch watcher (2026-11-04), whose rules were tuned on real titles. This is the cheap
first pass; the LLM (llm.py) then looks at the photo of anything that passes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MIN_YEN = 3000  # fixed prices below this are prints, prizes or scraps; real auctions start at ¥1

FRANCHISES: dict[str, dict[str, Any]] = {
    "broly": {
        "label": "Broly",
        "name": "Dragon Ball's Broly",
        "jp": ["ブロリー"],
        "en": ["broly"],
        "buyee": ["ブロリー"],
        "mercari": ["ブロリー セル画"],
        "ebay": "broly",
        "dragonball": True,  # bare セル is Cell, the villain: only セル画 says cel
        "genga": False,
    },
    "fma": {
        "label": "FMA",
        "name": "Fullmetal Alchemist",
        "jp": ["鋼の錬金術師", "ハガレン"],
        "en": ["fullmetal", "full metal alchemist", "hagaren"],
        "buyee": ["鋼の錬金術師", "ハガレン"],
        "mercari": ["鋼の錬金術師 セル画", "鋼の錬金術師 原画", "ハガレン セル画"],
        "ebay": "fullmetal alchemist",
        "genga": True,  # most FMA listings seen (2026-11-04) were 原画/動画 rather than cels
    },
    "eva": {
        "label": "Eva",
        "name": "Neon Genesis Evangelion (and the Rebuild films)",
        "jp": [
            "エヴァンゲリオン",
            "エヴァンゲリヲン",
            "ヱヴァンゲリヲン",
            "エヴァ",
            "渚カヲル",
            "綾波レイ",
            "初号機",
            "弐号機",
        ],
        "en": ["evangelion", "kaworu", "ayanami"],
        "buyee": ["エヴァンゲリオン", "エヴァ"],
        "mercari": ["エヴァンゲリオン セル画", "エヴァ 複製セル", "エヴァ セル画"],
        # Yahoo's own セル画 › さ行 › 新世紀エヴァンゲリオン: it holds titles the keywords miss (a typo).
        "category": "2084034853",
        "ebay": "evangelion",
        "genga": False,
    },
    "pokemon": {
        "label": "Pokémon",
        "name": "Pokémon",
        "jp": ["ポケモン", "ポケットモンスター", "ピカチュウ"],
        "en": ["pokemon", "pokémon", "pocket monster", "pikachu"],
        "buyee": ["ポケモン", "ポケットモンスター"],
        "mercari": ["ポケモン セル画", "ポケットモンスター セル画"],
        "ebay": "pokemon",
        "genga": False,
    },
    # Dragon Ball characters after Broly, in this order: a title naming two goes to the first.
    # Yahoo files them all in one ドラゴンボール category of some 600, too many to read every sweep.
    "vegeta": {
        "label": "Vegeta",
        "name": "Dragon Ball's Vegeta",
        "jp": ["ベジータ"],
        "en": ["vegeta"],
        "buyee": ["ベジータ"],
        "mercari": ["ベジータ セル画"],
        "ebay": "vegeta",
        "dragonball": True,  # bare セル is Cell, the villain: only セル画 says cel
        "genga": False,
    },
    "goku": {
        "label": "Goku",
        "name": "Dragon Ball's Son Goku",
        "jp": ["悟空"],
        "en": ["goku"],
        "buyee": ["悟空"],
        "mercari": ["悟空 セル画"],
        "ebay": "goku",
        "dragonball": True,  # bare セル is Cell, the villain: only セル画 says cel
        "genga": False,
    },
    "kiki": {
        "label": "Kiki",
        "name": "Kiki's Delivery Service",
        "jp": ["魔女の宅急便"],
        "en": ["kiki's delivery", "kikis delivery", "majo no takkyubin"],
        "buyee": ["魔女の宅急便"],
        "mercari": ["魔女の宅急便 セル画"],
        "category": "2084035786",
        "ebay": "kiki's delivery service",
        "genga": False,
    },
    "mononoke": {
        "label": "Mononoke",
        "name": "Princess Mononoke",
        "jp": ["もののけ姫"],
        "en": ["princess mononoke", "mononoke hime"],  # bare "mononoke" is also the 2007 series
        "buyee": ["もののけ姫"],
        "mercari": ["もののけ姫 セル画"],
        "category": "2084035780",
        "ebay": "princess mononoke",
        "genga": False,
    },
    "bebop": {
        "label": "Bebop",
        "name": "Cowboy Bebop",
        "jp": [
            "カウボーイビバップ",
            "カウボーイ・ビバップ",
        ],  # bare ビバップ is also Be-Bop High School
        "en": ["cowboy bebop"],
        "buyee": ["カウボーイビバップ"],
        "mercari": ["カウボーイビバップ セル画"],
        "category": "2084034539",
        "ebay": "cowboy bebop",
        "genga": False,
    },
}

JUNK = re.compile(
    r"一番くじ|フィギュア|カード|セル画風|セル風|印刷|プリント|ポスター|同人|"
    r"原画集|設定資料|シール|ステッカー|アクリル|クリアファイル|下敷き|DVD|Blu-?ray|ブルーレイ|雑誌|アニメージュ|"
    r"付録|ZENBU|カタログ|フュージョンワールド|アートシート|ハガキ|はがき|フィルム|35mm|版画|ミストグラフ|キャラファイン|PSA|ブースター|メモリアル原画アート|テレカ|"
    r"テレホンカード|書籍|文庫|ムック|ポストカード|キーホルダー|缶バッジ|タペストリー|ジグソー|プライズ|景品|リペイント|カスタム|"
    r"ichiban\s*kuji|sericel|seri-cel|gicl[eé]e|lithograph|fan\s*art|\bcustom\b|\bprize\b|cel\s+print|print(?:ed)?\s+cel|limited\s+print|print\s*club|figure|\bcards?\b|poster|cel-style|doujin|sticker|acrylic|printed",
    re.I,
)
# Cel Club (セル画倶楽部): mass-produced printed collectibles, never wanted. 俱 is the other form of 倶;
# matched on NFKC text so full-width ＣＥＬ ＣＬＵＢ is caught too.
CEL_CLUB = re.compile(
    r"セル画?\s*[倶俱]楽部|セル画?\s*クラブ|cel+u?\s*-?\s*club", re.I
)  # Cellu Club on eBay
# eBay sellers of drawings write "Genga not Cel", "Non Cel Layout": that "cel" is not a cel.
NOT_CEL = re.compile(r"\bno[nt]\s*-?\s*cels?\b", re.I)
REPRO = re.compile(r"複製|復刻|リプロ|レプリカ|reproduction|\brepro\b|replica|duplicate", re.I)
# "Cell" alone is a phone or a CD on eBay; it means a cel only after these words.
CEL = re.compile(r"セル|\bcels?\b|\b(?:production|animation|anime|original)\s+cells?\b", re.I)
GENGA = re.compile(
    r"原画|動画|修正|レイアウト|genga|douga|key\s*frame|animation\s*drawing|original\s*drawing",
    re.I,
)
# GAINAX's own limited reproduction cels (100 per cut, numbered, with certificate): wanted despite REPRO.
GAINAX_LIMITED = re.compile(
    r"限定|/\s*100|ガイナックス|gainax|公式|シリアル|of\s*100\b|numbered", re.I
)
# A reproduction cel in the title's own words: Japanese, or how eBay sellers write it in English.
REPRO_CEL = re.compile(
    r"複製セル|(?:reproduction|repro\.?|replica)\s+(?:animation\s+|anime\s+)?cell?s?\b", re.I
)


def scope(key: str) -> tuple[str, ...]:
    """The franchises one cel may be filed under: Broly, Vegeta and Goku share Dragon Ball cels (a
    cel of both is Vegeta by one title, Goku by another), so a relist is matched across all three.
    """
    if FRANCHISES[key].get("dragonball"):
        return tuple(k for k, f in FRANCHISES.items() if f.get("dragonball"))
    return (key,)


# In Dragon Ball titles セル alone is Cell (セル編, VS セル 完全体), not a cel.
DB_CELL = re.compile(r"セル(?!画)")
NOT_EVA = re.compile(r"エヴァーガーデン|エヴァーグリーン|エヴァー|エヴァンス")
# The other Goku: Journey to the West and its anime (悟空の大冒険, 最遊記, ぼくの孫悟空), Gokudō (極道).
# A title naming one of these is never Dragon Ball's Goku, whatever else it says.
NOT_GOKU = re.compile(
    r"悟空の大冒険|西遊記|最遊記|ぼくの孫悟空|saiyuki|journey\s+to\s+the\s+west|gokudo|goku\s+no\s+daiboken",
    re.I,
)


def franchise_of(title: str, extra: str = "", english: bool = False) -> str | None:
    """From the Japanese title; the translated text only when there is no Japanese title at all.

    Buyee's English translations name voice actors' other roles ("Rika Matsumoto ... Pokémon" on an Outlaw
    Star cel, 2026-11-04), so they are not trusted when a Japanese title exists.
    """
    text = NOT_EVA.sub("", title)
    # An English title (eBay) is the seller's own words, so it is trusted like a Japanese one.
    low = (title if english else "" if title.strip() else extra).lower()
    for key, f in FRANCHISES.items():
        if key == "goku" and (NOT_GOKU.search(text) or NOT_GOKU.search(low)):
            continue
        if any(t in text for t in f["jp"]) or any(t in low for t in f["en"]):
            return key
    return None


def classify(
    title: str,
    extra: str = "",
    yen: int | None = None,
    in_cel_category: bool = False,
    auction: bool = False,
    english: bool = False,
    hint: str = "",
) -> dict | None:
    """Franchise and kind for a listing worth alerting on, or None.

    title is the Japanese title; extra is any translated text (Buyee), used for the junk and kind checks.
    The price floor applies to fixed prices only: real cels go up on Yahoo at ¥1 starts.
    hint is the franchise of the Yahoo category the listing was found in. It names the franchise only
    when the title names none (a typo), and the result says so (hinted), since Yahoo's categories hold
    misfiled listings: the LLM must then confirm the franchise from the photo.
    """
    text = f"{title} {extra}"
    if CEL_CLUB.search(unicodedata.normalize("NFKC", text)):
        return None
    key = franchise_of(title, extra, english)
    hinted = not key and hint in FRANCHISES
    if hinted:
        key, in_cel_category = hint, True
    if not key:
        return None
    if not auction and yen is not None and yen < MIN_YEN:
        return None
    if (
        key == "eva"
        and REPRO.search(text)
        and REPRO_CEL.search(text)
        and GAINAX_LIMITED.search(text)
        and not JUNK.search(text)  # prizes, prints, fan art: never GAINAX's own
    ):
        return {"franchise": key, "kind": "gainax-limited", "hinted": hinted}
    if JUNK.search(text) or REPRO.search(text):
        return None
    cel_text = DB_CELL.sub("", text) if FRANCHISES[key].get("dragonball") else text
    if CEL.search(NOT_CEL.sub("", cel_text)) or in_cel_category and not GENGA.search(text):
        return {"franchise": key, "kind": "cel", "hinted": hinted}
    if GENGA.search(text) and FRANCHISES[key]["genga"]:
        return {"franchise": key, "kind": "genga", "hinted": hinted}
    return None


YEN_RE = re.compile(r"([\d,]+)\s*(?:円|YEN)")
# "£677.28" in English, "(677.28ポンド)" on Buyee's Japanese pages.
GBP_RE = re.compile(r"£\s*\|?\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*ポンド")


def yen_of(text: str) -> int | None:
    m = YEN_RE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


def gbp_of(text: str) -> float | None:
    m = GBP_RE.search(text)
    return float((m.group(1) or m.group(2)).replace(",", "")) if m else None
