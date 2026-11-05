"""Which listings are worth cataloguing: franchise from the Japanese title, junk and prints dropped.

Ported from the celwatch watcher (2026-11-04), whose rules were tuned on real titles. This is the cheap
first pass; the LLM (llm.py) then looks at the photo of anything that passes.
"""

from __future__ import annotations

import re
from typing import Any

MIN_YEN = 3000  # fixed prices below this are prints, prizes or scraps; real auctions start at ¥1

FRANCHISES: dict[str, dict[str, Any]] = {
    "broly": {
        "label": "Broly",
        "jp": ["ブロリー"],
        "en": ["broly"],
        "buyee": ["ブロリー"],
        "mercari": ["ブロリー セル画"],
        "genga": False,
    },
    "fma": {
        "label": "FMA",
        "jp": ["鋼の錬金術師", "ハガレン"],
        "en": ["fullmetal", "full metal alchemist", "hagaren"],
        "buyee": ["鋼の錬金術師", "ハガレン"],
        "mercari": ["鋼の錬金術師 セル画", "鋼の錬金術師 原画", "ハガレン セル画"],
        "genga": True,  # most FMA listings seen (2026-11-04) were 原画/動画 rather than cels
    },
    "eva": {
        "label": "Eva",
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
        "genga": False,
    },
    "pokemon": {
        "label": "Pokémon",
        "jp": ["ポケモン", "ポケットモンスター", "ピカチュウ"],
        "en": ["pokemon", "pokémon", "pocket monster", "pikachu"],
        "buyee": ["ポケモン", "ポケットモンスター"],
        "mercari": ["ポケモン セル画", "ポケットモンスター セル画"],
        "genga": False,
    },
}

JUNK = re.compile(
    r"一番くじ|フィギュア|カード|セル画風|セル風|セル倶楽部|セル画倶楽部|セルクラブ|印刷|プリント|ポスター|同人|"
    r"原画集|設定資料|シール|ステッカー|アクリル|クリアファイル|下敷き|DVD|Blu-?ray|ブルーレイ|雑誌|アニメージュ|"
    r"付録|ZENBU|カタログ|フィルム|35mm|版画|ミストグラフ|キャラファイン|PSA|ブースター|メモリアル原画アート|テレカ|"
    r"テレホンカード|書籍|文庫|ムック|ポストカード|キーホルダー|缶バッジ|タペストリー|ジグソー|プライズ|景品|リペイント|カスタム|"
    r"ichiban\s*kuji|figure|\bcards?\b|poster|cel\s*club|cel-style|doujin|sticker|acrylic|printed",
    re.I,
)
REPRO = re.compile(r"複製|復刻|リプロ|reproduction|replica|duplicate", re.I)
CEL = re.compile(r"セル|\bcels?\b", re.I)
GENGA = re.compile(
    r"原画|動画|修正|レイアウト|genga|douga|key\s*frame|animation\s*drawing|original\s*drawing",
    re.I,
)
# GAINAX's own limited reproduction cels (100 per cut, numbered, with certificate): wanted despite REPRO.
GAINAX_LIMITED = re.compile(r"限定|/\s*100|ガイナックス|gainax|公式|シリアル", re.I)


NOT_EVA = re.compile(r"エヴァーガーデン|エヴァーグリーン|エヴァー|エヴァンス")


def franchise_of(title: str, extra: str = "") -> str | None:
    """From the Japanese title; the translated text only when there is no Japanese title at all.

    Buyee's English translations name voice actors' other roles ("Rika Matsumoto ... Pokémon" on an Outlaw
    Star cel, 2026-11-04), so they are not trusted when a Japanese title exists.
    """
    text = NOT_EVA.sub("", title)
    low = extra.lower()
    for key, f in FRANCHISES.items():
        if any(t in text for t in f["jp"]) or not title.strip() and any(t in low for t in f["en"]):
            return key
    return None


def classify(
    title: str,
    extra: str = "",
    yen: int | None = None,
    in_cel_category: bool = False,
    auction: bool = False,
) -> dict | None:
    """Franchise and kind for a listing worth alerting on, or None.

    title is the Japanese title; extra is any translated text (Buyee), used for the junk and kind checks.
    The price floor applies to fixed prices only: real cels go up on Yahoo at ¥1 starts.
    """
    text = f"{title} {extra}"
    key = franchise_of(title, extra)
    if not key:
        return None
    if not auction and yen is not None and yen < MIN_YEN:
        return None
    if (
        key == "eva"
        and REPRO.search(text)
        and re.search(r"複製セル|reproduction cel", text, re.I)
        and GAINAX_LIMITED.search(text)
        and not re.search(r"セル倶楽部|セル画倶楽部|cel\s*club|景品|プライズ", text, re.I)
    ):
        return {"franchise": key, "kind": "gainax-limited"}
    if JUNK.search(text) or REPRO.search(text):
        return None
    if CEL.search(text) or in_cel_category and not GENGA.search(text):
        return {"franchise": key, "kind": "cel"}
    if GENGA.search(text) and FRANCHISES[key]["genga"]:
        return {"franchise": key, "kind": "genga"}
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
