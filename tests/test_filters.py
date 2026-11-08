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


def test_new_franchises_on_real_titles():
    # Buyee and Mercari titles seen 2026-11-08
    assert k("15％値下げ　魔女の宅急便　キキ　おソノ　セル画と動画　スタ") == ("kiki", "cel")
    assert k("16％値下げ　もののけ姫　アシタカ　セル画　スタジオジブリ　") == ("mononoke", "cel")
    assert k("カウボーイビバップ セル画 エド エドワード・ウォン・ハウ・") == ("bebop", "cel")
    assert k("ドラゴンボールZ　ベジータ　セル画　鳥山明　") == ("vegeta", "cel")
    assert k("ドラゴンボールZ　孫悟空　セル画と動画　鳥山明　") == ("goku", "cel")
    # a title naming two goes to the first: Broly, then Vegeta, then Goku
    assert k("ドラゴンボール 孫悟空 ベジータ セル画") == ("vegeta", "cel")
    assert k("劇場版 ドラゴンボールZ ブロリー 孫悟空 セル画") == ("broly", "cel")
    assert franchise_of("", "Princess Mononoke Ashitaka cel") == "mononoke"
    assert franchise_of("", "Mononoke Kusuriuri cel") is None  # the 2007 series
    assert franchise_of("ビー・バップ・ハイスクール セル画") is None


def test_the_other_goku_and_cell_the_villain():
    assert k("劇場版 幻想魔伝 最遊記 Requiem 孫悟空 セル画", cat=False, yen=9000) is None
    assert k("悟空の大冒険 セル画 手塚治虫", cat=False, yen=9000) is None
    # In Dragon Ball titles bare セル is Cell; only セル画 says cel
    assert k("ドラカプ　ベジータ　VS セル　完全体", cat=False, yen=9000, auction=False) is None
    assert k(
        "ドラゴンボールZ ベジータ 人造人間・セル編 セル画　超サイヤ人", cat=False, yen=9000
    ) == ("vegeta", "cel")
    assert k("ポケモン セル 2枚", cat=False, yen=9000) == (
        "pokemon",
        "cel",
    )  # elsewhere it is a cel


def test_new_franchise_prints_dropped():
    for t in (
        "魔女の宅急便　セル画　実寸レプリカ",
        "【激レア】ドラゴンボールZ　孫悟空 界王拳　セル画アートシート　鳥山明",
        "フュージョンワールド 破壊王子 SR ベジータ パラレル セル画　4枚",
        "魔女の宅急便他 アニメセル画販売会 限定ハガキ 4枚 非売品物",
        "【激レア】もののけ姫　ポスター　ジブリ　宮崎駿　B2サイズ　",
    ):
        assert k(t, yen=9000, auction=False) is None, t


def test_category_hint_names_only_a_title_that_names_none():
    typo = classify("エゥンゲリオン セル画", "", 60000, True, True, hint="eva")
    assert typo == {"franchise": "eva", "kind": "cel", "hinted": True}
    named = classify("魔女の宅急便 セル画", "", 60000, True, True, hint="eva")
    assert named == {"franchise": "kiki", "kind": "cel", "hinted": False}
    assert classify("エゥンゲリオン セル画", "", 60000, True, True) is None
    # the category's junk is still junk (a Sailor Moon 同人 cel filed under Evangelion)
    assert (
        classify("美少女戦士セーラームーン 同人 セル画", "", 9000, True, True, hint="eva") is None
    )


def test_kiki_with_a_typographic_apostrophe():
    c = classify("Kiki’s Delivery Service production cel", "", 10000, False, False, english=True)
    assert c and c["franchise"] == "kiki"
