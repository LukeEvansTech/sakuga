"""Filter tests on real titles seen 2026-11-04 (ported from the celwatch watcher)."""

from sakuga.filters import classify, franchise_of, gbp_of


def k(title, extra="", yen=None, cat=True, auction=True):
    c = classify(title, extra, yen, cat, auction)
    return c and (c["franchise"], c["kind"])


def test_real_cels_pass():
    assert k("劇場版　ドラゴンボールZ ブロリー　セル画と動画のセット　鳥", yen=330000) == (
        "broly",
        "cel",
    )
    assert k("新世紀エヴァンゲリオン 弐号機 セル画 背景画 庵野秀明 貞", yen=498000) == (
        "eva",
        "cel",
    )
    assert k("セル画　ポケモン　ポケットモンスター　ピカチュウ　ロケット団", yen=51) == (
        "pokemon",
        "cel",
    )  # ¥51 auction start


def test_gainax_limited_repro_is_its_own_kind():
    t = "エヴァンゲリオン 公式複製セル画 渚カヲル 第24話 最後のシ者 GAINAX 2002年当時物 額装品 庵野秀明 限定100部 シリアルNo.04/100"
    assert k(t, yen=300000) == ("eva", "gainax-limited")
    assert k("限定複製セル画　新世紀エヴァンゲリオン　渚カヲル GAINAX") == ("eva", "gainax-limited")


def test_junk_and_prints_dropped():
    assert k("新世紀エヴァンゲリオンセル画倶楽部", yen=6000) is None
    assert (
        k("エヴァンゲリオン セル倶楽部 限定 印刷セル画 加持リョウジ", cat=False, auction=False)
        is None
    )
    assert k("ドラゴンボール 複製セル画 複製原画 ブロリー 鳥山明 DRAGON BALL", yen=5000) is None
    assert k("ブロリー SR★ ST01-050 セル画風パラレル DBFW", cat=False, auction=False) is None
    assert (
        k(
            "未使用 ドラゴンボール 一番くじ E賞 メモリアル原画アートPLUS ブロリー②",
            cat=False,
            auction=False,
        )
        is None
    )
    assert k("希少「エヴァンゲリオン/フォーエヴァー」エヴァ書籍.井手功二", yen=580) is None
    assert (
        k("⚠️複製セル画 プライズ品 新世紀エヴァンゲリオン 渚カヲル", cat=False, auction=False)
        is None
    )


def test_price_floor_only_for_fixed_price():
    assert k("ポケモン セル画", yen=500, cat=False, auction=False) is None
    assert k("ポケモン セル画", yen=500, cat=False, auction=True) == ("pokemon", "cel")


def test_franchise_from_japanese_not_translation():
    assert (
        franchise_of(
            "星方武侠アウトロースターセル画　ジム・ホーキング 松本梨香（",
            "Outlaw Star ... Pokémon voice",
        )
        is None
    )
    assert franchise_of("ヴァイオレット・エヴァーガーデン 描き下ろし生動画") is None
    assert franchise_of("", "Neon Genesis Evangelion Rei cel") == "eva"


def test_genga_only_where_wanted():
    assert k(
        "k６０　鋼の錬金術師直筆動画【ウィンリー・ロックベル】", cat=False, auction=False, yen=None
    ) == ("fma", "genga")
    assert k("ドラゴンボール ブロリー 原画 修正", cat=False, auction=False, yen=None) is None


def test_gbp_from_both_page_languages():
    assert gbp_of("(677.28ポンド)") == 677.28
    assert gbp_of("£1,503") == 1503.0
    assert gbp_of("575円") is None


def test_cel_club_in_any_spelling_is_dropped():
    # 俱 is the other common form of 倶; Cel Club is never wanted, even with GAINAX words.
    assert k("新世紀エヴァンゲリオン セル画俱楽部 綾波レイ", yen=8000) is None
    assert k("エヴァンゲリオン セル画クラブ 碇シンジ", yen=8000) is None
    assert k("エヴァンゲリオン セル画 ＣＥＬ ＣＬＵＢ アスカ", yen=8000) is None
    assert k("エヴァンゲリオン セル画 アスカ", "Evangelion Cell Club Asuka", yen=8000) is None
    t = "エヴァンゲリオン 公式複製セル画 GAINAX 限定100部 セル画 倶楽部"
    assert k(t, yen=30000) is None
