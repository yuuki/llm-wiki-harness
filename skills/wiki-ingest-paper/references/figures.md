# 論文図表のキャプション座標クロップ

`wiki-ingest-paper` の従属資料。方針(コンタクトシート → 判断不能セルだけ Read → 本文参照は全件)は SKILL 側が正本である。

`pdf.js` が抽出する `image-*.png` はラスター埋め込みだけである。ベクター描画(taxonomy・アーキテクチャ・フロー)は取れない。チェックリストに載って `image-*.png` に無い図表は、この手順で全件クロップする。「埋め込み画像が取れなかったから諦める」は認めない。

座標を手で拾ってページ全幅を切らない。二段組み ACM では本文が混入する。キャプション探索もブロック先頭の `startswith("Fig.")` にしない。キャプションはブロック途中にあり、`Figure 2 presents` のような本文言及と紛らわしい。

---

## 起動

`pip install` / `uv pip install` はサンドボックスで拒否される。システムの python には PyMuPDF が無いことが多い。次でヘルパーを呼ぶ。

```bash
mkdir -p "wiki/sources/_attachments/<slug>"
uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 scripts/crop-paper-figures.py \
  --pdf ".raw/papers/<slug>.pdf" \
  --out "wiki/sources/_attachments/<slug>" \
  --id fig:1:fig01-architecture.png \
  --id fig:2:fig02-results.png \
  --id tab:1:table1-dataset.png
```

- `--cache-dir "$TMPDIR/uv-cache"` が必須。省略すると `~/.cache/uv/.git` への書き込みで落ちる
- `--id` は `fig:番号:ファイル名.png` または `tab:番号:ファイル名.png`
- 各 `--id` について JSON 1 行。`ok: false` は `reason` を見て手修正する
- `review: true` は本文混入のサイズ合図(ACM 全幅スライス等)。**そのファイルは Read して目視する**
- `slide-` / `fig-page` / `page-` / `poster` はスキップする(論文図ではない)

ヘルパーがやること:

1. `page.search_for("Figure N:")` / `Fig. N.` / `Table N:` だけを使う(末尾スペースの `Figure N ` は本文言及に当たるので使わない)
2. キャプション行は同じ行・同じ段だけ伸ばす。隣カラムの同一ベースラインは段間で切る
3. キャプションが段をまたぐときだけ全幅。一段のキャプションは `page.rect.width / 2` でクランプする
4. 描画・画像の塊をキャプション直上からギャップで止め、同一ページの直前キャプションより上へは伸ばさない
5. 表は高さ 200 px 未満(キャプションだけ)を捨てる。図は 110 px 未満を捨てる

`uv run` がキャッシュ待ちで止まる、または sandbox が拒否したら、同じコマンドを権限付きで再実行する。手で `fitz` の全幅 `clip` を組まない。

---

## 目視

クロップ後、次は必ず Read する。

- ヘルパーが `review: true` を付けたファイル
- `ok: false` で手修正したファイル
- コンタクトシート上で位置が疑わしいセル

合格: 図(または表)とキャプションだけで、左右の本文カラム・要旨・ヘッダーが写っていない。

落ちる典型:

| 症状 | 原因 | 直し方 |
|---|---|---|
| 図の下または横に二段組み本文 | 全幅クリップ / キャプションを隣段と結合 | ヘルパー再実行。手修正するならキャプション中心の段だけ |
| キャプションだけの細い帯 | 表罫が高さ 0 で無視された / 探索が本文「Table N summarizes」に当たった | ヘルパーは後者を拾わない。前者は表 bbox を手で足す |
| 図が別の段落 | `Figure 2 presents` をキャプションにした | `Figure N:` / `Fig. N.` だけを使う |
| 同一ページの前の図まで含む | 直前キャプションで切っていない | ヘルパー任せ。手修正なら前キャプションの y1 を上端にする |
| `fig-page*` / スライド / ポスター | 論文図ではない | 作らない。既存なら埋め込まない |

ページヘッダー(多くは y < 65 pt)とランニングヘッダーは範囲に入れない。

---

## 手修正が必要なとき

ヘルパーが `no_caption` / `thin` を返した番号だけ、そのページを `page.get_text("blocks")` で見て `clip` する。**既定の x はページ全幅にしない。** キャプションの x 中心がページ中央より左なら左段、右なら右段。`SCALE = 3.0`(216 DPI)。キャプションを正規表現で探すなら、`Figure N:` / `Fig. N.` の区切りまで含めて大文字小文字を区別する。`Figure N `(末尾スペース)や、`re.I` を付けた `[A-Z]` は、本文の「Table 2 summarizes」のような参照文に当たる。

```bash
uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 - <<'PY'
import fitz
doc = fitz.open(".raw/papers/<slug>.pdf")
page = doc[<0indexed>]
# キャプション検索で y0 を確認してから clip する
print(page.search_for("Figure N:"))
pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(x0, y0, x1, y1))
pix.save("wiki/sources/_attachments/<slug>/<file>.png")
PY
```

同じ PNG 名で上書きする。`.raw/` の PDF は触らない。
