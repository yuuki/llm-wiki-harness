# 論文図表のキャプション座標クロップ

`wiki-ingest-paper` の従属資料。SKILL の Step 0.5 §1.5 から移した PyMuPDF 手順である。方針(コンタクトシート → 判断不能セルだけ Read → 本文参照は全件)は SKILL 側が正本である。

`pdf.js` が抽出する `image-*.png` はラスター埋め込み画像のみである。ベクター描画(taxonomy 図・アーキテクチャ図・フロー図)はこの方法では取れない。チェックリストに載った図表が `image-*.png` に無ければ、この手順で全件クロップする。「埋め込み画像が取れなかったから図表は諦める」は認めない。

---

## 起動

`pip install` / `uv pip install` はサンドボックスで拒否される。代わりに次を使う。

```bash
uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 - <<'PY'
import fitz
# ここに Python コードを書く
PY
```

- `uv run --with` は `pip:*` / `uv pip:*` の deny ルールに引っかからない
- `--cache-dir "$TMPDIR/uv-cache"` が必須。省略すると `~/.cache/uv/.git` への書き込みで `Operation not permitted` になる
- ヒアドキュメント(`<<'PY' ... PY`)でスクリプトを渡すと、シェルの `!` history expansion と衝突しない

---

## Step A: 全ページのキャプション座標を一括探索する

```bash
uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 - <<'PY'
import fitz

SLUG = "<slug>"
doc = fitz.open(f".raw/papers/{SLUG}.pdf")
print("pages:", len(doc))

for i, page in enumerate(doc):
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt, bn, bt = b
        if bt == 0 and txt.strip().startswith("Fig."):
            print(f"  PDF page {i+1}: y=[{y0:.1f},{y1:.1f}] x=[{x0:.1f},{x1:.1f}] | {txt.strip()[:100]}")
PY
```

---

## Step B: 図のあるページの全ブロックを確認して上端を特定する

```bash
uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 - <<'PY'
import fitz

SLUG = "<slug>"
doc = fitz.open(f".raw/papers/{SLUG}.pdf")

# Step A で判明したページ番号(1-indexed)を列挙する
for pg_1indexed in [6, 8, 12]:
    page = doc[pg_1indexed - 1]
    print(f"\n=== PDF page {pg_1indexed} ===")
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt, bn, bt = b
        kind = "IMG" if bt == 1 else "TXT"
        print(f"  {kind} y=[{y0:.1f},{y1:.1f}] | {txt.strip()[:80] if bt==0 else '(image)'}")
PY
```

図の上端は「図の直前にある本文テキストブロックの y1」から推定する。ページヘッダー(多くは y < 65 pt)はクロップ範囲から除く。

---

## Step C: 図表領域をクロップして attachment に保存する

```bash
mkdir -p "wiki/sources/_attachments/<slug>"

uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 - <<'PY'
import fitz

SLUG = "<slug>"
OUT  = f"wiki/sources/_attachments/{SLUG}"
doc  = fitz.open(f".raw/papers/{SLUG}.pdf")
SCALE = 3.0  # 216 DPI (72 pt × 3)
mat   = fitz.Matrix(SCALE, SCALE)

# (pdf_page_0indexed, y_top, y_bottom, x_left, x_right, filename)
# y_top   : 直前テキストブロックの y1(図の上端の手がかり)
# y_bottom: キャプション y0 - 余白(図の下端)
crops = [
    (5,  540, 745, 30, 562, "fig01-architecture.png"),
    (7,   62, 291, 30, 562, "fig02-results.png"),
]

for pg_idx, y0, y1, x0, x1, fname in crops:
    page = doc[pg_idx]
    pix  = page.get_pixmap(matrix=mat, clip=fitz.Rect(x0, y0, x1, y1))
    pix.save(f"{OUT}/{fname}")
    print(f"saved {OUT}/{fname}  ({pix.width}x{pix.height}px)")
PY
```

クロップ後は Read ツールで画像を開いて内容を目視確認する。ページヘッダー・フッターが混入していたら `y_top` / `y_bottom` を調整して再実行する。

**注意点**:

- 図本体(ベクター・埋め込みとも)はテキストブロックに出ないため、図の上端は直接取得できない。同一ページ内で図の上に本文テキストがある場合は、直前のテキストブロック y1 を上端として使う。
- 1 ページに複数の図がある場合は、前の図のキャプション y1 を次の図の上端の手がかりにする。
- ページヘッダー/フッターが混入しないよう y 範囲を調整する。
