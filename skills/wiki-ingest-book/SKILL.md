---
name: wiki-ingest-book
description: "Book-specialized wiki ingest. Use when the source is a book: a whole-book PDF, a set of per-chapter web pages (SRE Book style), or a fragment of a book (a single chapter PDF/text). Stores the immutable original under `.raw/books/<slug>/`, splits it by chapter, and files ONE source page PER CHAPTER into `wiki/sources/` plus ONE hub entity page for the book itself into `wiki/entities/`. Use this — not wiki-ingest-paper (papers) nor wiki-ingest-slides (slide decks) nor the generic wiki-ingest (articles) — whenever the material is a book or book chapter. Triggers on: 'この本を wiki に', '書籍を ingest', 'book を取り込んで', '本の PDF を章ごとに wiki 化', '章ごとに分割して取り込んで', 'ingest this book', 'この章だけ取り込んで'. Copyrighted book content: every chapter source page gets `publish: false` so Obsidian Publish never exposes it."
---

# wiki-ingest-book: 書籍特化の wiki 取り込み

書籍を**章単位**で wiki レイヤーに取り込む。原本(書籍 PDF または章別ウェブページ)を `.raw/books/<slug>/` に不変原本として置き、**1 章 = 1 source ページ**を `wiki/sources/` に作り、**書籍そのものを表すハブ entity ページを 1 つ** `wiki/entities/` に作って各章 source へリンクする。既存の SRE Book / SRE Workbook の運用(章 source 群 + `SRE Book.md` ハブ)を正式化したものだ。

書籍全体を 1 つの source ページに押し込まない。逆に、**最初から書籍の断片(1 章分)が入力された場合は分割しない**(source は 1 枚、book entity は作成/更新する)。

> [!important] 最初に必ず読むこと
> wiki を操作する前に [`wiki/meta/conventions.md`](../../wiki/meta/conventions.md)・[`wiki/meta/japanese-style.md`](../../wiki/meta/japanese-style.md)・[`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md) を読む。frontmatter・命名・言語(日本語常体)・出典規約は**標準形式より conventions が優先**される。本スキルの記述と conventions が食い違ったら conventions に従う。
>
> **カタログ類は Read しない**: `wiki/index.md`・`wiki/{sources,entities,concepts}/_index.md`・`wiki/hot.md`・`wiki/log.md` の全文を `Read` してはならない。既存ページの発見は `wiki-resolve.py`、カタログ更新は `wiki-catalog.py`、本文の部分読みは `wiki-excerpt.py` に任せる。
>
> 章担当 subagent へ渡す手順は [`references/subagent-brief.md`](references/subagent-brief.md) が正本である。フル SKILL を subagent に渡さない。抽出ファイルの形は [`references/extract-schema.md`](references/extract-schema.md)。図表の長いクロップ手順は [`references/figures.md`](references/figures.md)、fan-out の逸話と失敗事例は [`references/fan-out.md`](references/fan-out.md)。

> [!warning] 著作権(publish 無効化)
> 書籍は著作権コンテンツである。本スキルが作る**章 source ページ(断片含む)の frontmatter には必ず `publish: false` を入れ**、Obsidian Publish への公開を無効化する。省略してはならない。book entity ページ(書誌と章リンクのハブ)は要約・書誌情報のみで本文の複製を含まないため対象外だが、本文引用を厚く書いた場合は同様に `publish: false` を付ける。

---

## いつ使うか(兄弟スキルとの境界)

| 入力・意図 | 使うスキル | 出力 |
|---|---|---|
| **書籍**(全体 PDF / 章別ウェブページ / 章の断片)を wiki 化 | **wiki-ingest-book(本スキル)** | `.raw/books/<slug>/` + `wiki/{sources,entities,concepts}/` |
| 研究論文(arXiv・DOI・会議論文 PDF) | `wiki-ingest-paper` | `.raw/papers/` + wiki |
| スライド PDF・SpeakerDeck | `wiki-ingest-slides` | `.raw/slides/<slug>/` + wiki |
| 技術記事・ブログ等の単発 URL | `wiki-ingest` | `.raw/articles/` + wiki |

判断基準: **入力が書籍(単行本・技術書・教科書・オンライン公開書籍)なら本スキル**。論文集(proceedings)は論文単位で `wiki-ingest-paper` を使う。

---

## 取り込み前のセットアップ確認

汎用 `wiki-ingest` / `wiki-ingest-paper` と同じ前提に従う(詳細はそちらを参照)。要点:

- **Transport = filesystem 固定**。**書き込みの正はヘルパー**(`wiki-page-write.py` / `wiki-append.py`。下記)。ヘルパーが落ちても新規 source / entity / concept を `Write` で作らない。止めて報告する。`Edit` はヘルパーで表現できない既存ページの 1 箇所修正に限り、その場合だけ `wiki-lock.sh` を手動で使う。**ヘルパー呼び出しを `wiki-lock.sh` で包まない**(自己デッドロック)。
- **書き込みは 2 つのヘルパーに束ねる**(呼び出し 1 回 = 常駐文脈 1 回分)。新規の章 source・book entity・concept は `python3 scripts/wiki-page-write.py --batch spec.json`(採番・conventions 検証・lock・アトミック書き込みを 1 回で。`publish: false` の欠落も検出する)、既存ページへの追記は `python3 scripts/wiki-append.py --batch spec.json`。**1 ページに複数回 Edit を撃たない。**
- **Lock はヘルパーが取る**: 上記 2 つは `wiki-lock.sh` と同じロックファイルを内部で取得・解放する(包むと自己デッドロック)。ヘルパーで表現できない既存ページの 1 箇所修正だけ、`Edit` を手動で `bash scripts/wiki-lock.sh acquire <path>` / `release <path>` に囲む。新規を `Write` で作らない。`wiki-catalog.py` は自己 lock するので包まない。
- **DragonScale address**: 新規ページ(source・entity・concept)の `address: c-NNNNNN` は `wiki-page-write.py` が採番して先頭キーに挿入する。エージェントは `allocate-address.sh` を直接実行しない(ヘルパー外の採番は失敗する)。`--address` は既に採番済みの値を渡すときだけ。既存ページ更新時は再採番しない。
- **`scripts/` は vault ルート直下**。カレントディレクトリが vault ルートである前提の相対パスでヘルパーを呼ぶ。
- **Manifest デルタ追跡**: `.raw/.manifest.json`(§ 再取り込み判定)。

---

## 入力形態の判定(最初に必ず行う)

| 形態 | 入力例 | フロー |
|---|---|---|
| **(a) 書籍全体 PDF** | ローカル PDF / PDF URL | Step 0 で `fetch-book.sh` → 章分割 → Step 0.3 で章リスト確定 → 全章 fan-out。book entity を新規作成 |
| **(b) 章別ウェブページ群** | SRE Book 型の章 URL リスト・目次ページ | `fetch-book.sh` は使わない。各章を defuddle で clean markdown 化して `.raw/books/<slug>/chapters/ch-NN.md` に保存。URL リスト = 章リストなので目次検出は不要 |
| **(c) 書籍の断片(1 章分)** | 1 章分の PDF・テキスト・単一の章 URL | **分割しない。source は 1 枚**。book entity は作成または更新(下記) |

**(c) 断片の判定基準**: ユーザーが「第 N 章」「chapter」と明示している / PDF が概ね 60 ページ未満 / 目次検出が単一章相当しか返さない / ファイル名・冒頭に "Chapter N" を含む。曖昧なら `AskUserQuestion` で「書籍全体として章分割するか、この断片 1 枚として扱うか」を確認する。

断片の raw は `.raw/books/<book-slug>/chapters/` に置き、**書籍全体と同じ slug を共有する**。後日、同じ書籍の別の章や全体 PDF を取り込むとき、同一ディレクトリに合流させるためだ。

**取り込み範囲**: 既定は**全章**。ユーザーが「3〜5 章だけ」のように範囲指定したらその範囲のみ取り込む(未取り込み章は book entity に列挙しない)。**20 章を超える長編**は着手前に章リスト(章番号・題・ページ範囲)を提示してユーザーの確認を得る。

---

## Step 0: 原本取得と章分割(`scripts/fetch-book.sh`)

入力形態 (a)(および (c) の章 PDF)の取得・章分割は決定論的なので、ヘルパーに任せる。

```bash
# PDF URL / ローカル PDF パスのいずれでも可。slug は書籍が特定できる安定名を明示する
bash scripts/fetch-book.sh "<pdf-url|local-pdf-path>" "<slug>"
# 例の出力(key=value):
#   pdf=.raw/books/<slug>/<slug>.pdf
#   txt=.raw/books/<slug>/<slug>.txt
#   slug=<slug>
#   pages=504
#   title=<PDF メタデータの title、空可>
#   year_hint=2012
#   toc=.raw/books/<slug>/toc.txt
#   chapters_dir=.raw/books/<slug>/chapters
#   chapters_count=17
#   chapter_01=22-39|Chapter 1 Introduction to Reliability Engineering
#   chapter_02=40-91|Chapter 2 Reliability Mathematics
#   ...
```

ヘルパーがやること:
1. **入力解決とダウンロード**: URL は `curl` で取得、ローカル PDF はコピー。先頭 `%PDF` シグネチャを検証し、HTML/エラーページを原本として残さない。既存 `.raw/books/<slug>/` にファイルがあれば停止する(`--force` でのみ上書き)。
2. **テキスト抽出**: `pdftotext -layout` で全文 `<slug>.txt` を生成。
3. **章検出**: PyMuPDF(`uv run --with pymupdf`)の `get_toc()` から章境界を決定。アウトラインが無い PDF は本文ページ先頭の章見出し(`Chapter N` / `第N章`)走査にフォールバックする。検出結果と全アウトラインは `toc.txt` に残る。
4. **章分割**: 章ごとにページ範囲テキストを `chapters/ch-NN.txt`(2 桁ゼロ埋め)へ書き出す。

### `.raw/books/<slug>/` レイアウト

```
.raw/books/<slug>/
  <slug>.pdf            # 全体 PDF(入力 a のみ。断片 c は chapters/ 配下に置く)
  <slug>.txt            # pdftotext -layout の全文
  toc.txt               # 検出目次(outline| 行と chapter| 行)
  chapters/ch-01.txt    # 章別テキスト(b は .md、c の断片 PDF は .pdf + .txt)
  images/images.json    # extract-paper-images.mjs の出力(Step 0.5)
  images/*.png          # 埋め込み画像
```

既存のフラット置き 2 ファイル(`.raw/books/practical-reliability-engineering-2012.{pdf,txt}`)は**移行しない・温存**。新規 ingest はすべて `<slug>/` ディレクトリ方式を使う。

### 章検出の確認と失敗時のフロー

`chapters_count` が想定と食い違うときの長い事例と `--chapters` の組み立て例は [`references/fan-out.md`](references/fan-out.md) の「章検出の失敗事例」に移した。ここでは次に何をするかだけを書く。

- **必ず `chapter_NN=` 行と `toc.txt` を突き合わせる**(序文・付録・索引の混入、章数の妥当性)。
- **多すぎる**: 日本語技術書は節見出しの誤検出を疑う。`WARN:`(`厳密パターン 0 件` または `chapters_count>20`)を見逃さない。`toc.txt` の `chapter|` 行を確認する。
- **少なすぎる**: Part 単位への化けを疑う。`chapter_NN=` の題に「部」「Part」「序文」「目次」「索引」「奥付」が混じっていたら、level 2 から `--chapters` を組み立てて再実行する。**末尾に索引・奥付を切り離すダミー章を 1 つ置く。**
  ```bash
  awk -F'|' '/^outline\|/ && $2<=2' .raw/books/<slug>/toc.txt
  awk -F'|' '/^outline\|/ && $2<=1' .raw/books/<slug>/toc.txt
  ```
  `chapters_count` が想定と食い違ったら、まず level 1 を並べて章がそこにあるかを見る。
- **部・章・節が同じ outline level に同居する**ときは level に頼らず章題のパターンで絞る。章題もこの出力から確定する(`chapter_NN=` の題は当てにならない)。
  ```bash
  awk -F'|' '/^outline\|/ && $4 ~ /^[0-9]+章 |^Chapter [0-9]+ /' .raw/books/<slug>/toc.txt
  ```
- **`--chapters` の章番号は `ch-NN` のラベル**で、連番でも昇順でもなくてよい。本編の最大章番号より大きい番号で部の序論・むすび・序文を切り出し、末尾の捨て番号で付録・索引を捨てる。
- 本文走査フォールバックは **±1 ページの誤差**があり得る。章冒頭を `head` で確認し、ずれていれば `--chapters` で再実行する。
- **章末の次章・次部の扉混入は正常な副作用**。再分割せず、該当章の subagent に「扉の内容は書かない」と伝える。
- `chapters_count=0` なら `toc.txt` または全文目次から章リスト案を作り、ユーザー確認のうえ手動指定で再実行する:
  ```bash
  bash scripts/fetch-book.sh --force --chapters "1=21,2=39,3=91,..." "<pdf>" "<slug>"
  ```
- **アウトライン 0 件**(`outline|` が無いスキャン PDF 等)は次節の 3 手順で復元する。
- 序文も捨て番号で切り出しておく。source 化するかは Step 0.3 で決める。
- **OCR テキスト層ありはスコープ内、テキストが取れないスキャン PDF はスコープ外。** `pdftotext` が空なら停止する。OCR 本は briefing に「読み取れない箇所は書かない」と書く。図は [`references/figures.md`](references/figures.md) のスキャン PDF 警告を読む。

### アウトラインが無い PDF から章境界を復元する

`toc.txt` に `outline|` 行が 1 件も無いとき。全文テキストの目次とランニングヘッダから復元する。

**1. 目次から章の印字ページ番号を読む。**

```bash
grep -n -m 40 -E "^\s*(Contents|目次|Preface|CHAPTER|Chapter|[0-9]+\s+[A-Z])" .raw/books/<slug>/<slug>.txt | head -40
```

**2. 印字ノンブルと PDF ページのオフセットを求める。** 改ページ(`\f`)で割ると PDF ページと 1 対 1。

```bash
python3 - <<'PY'
t = open('.raw/books/<slug>/<slug>.txt', encoding='utf-8', errors='replace').read()
pages = t.split('\f')
print("pdf pages:", len(pages))
for i, p in enumerate(pages[:25], start=1):
    head = " | ".join([l.strip() for l in p.split('\n')[:6] if l.strip()])[:110]
    print(i, '::', head)
PY
```

オフセットを決めたら `--chapters` で再分割する。組み立て例は [`references/fan-out.md`](references/fan-out.md)。

**3. 各章ファイルの冒頭数行を目視する。** 章題と章番号が先頭に来ていれば境界は正しい。

```bash
for f in .raw/books/<slug>/chapters/ch-*.txt; do echo "=== $(basename $f)"; grep -v '^\s*$' "$f" | head -4; done
```

### 入力形態 (b): 章別ウェブページの取得

汎用 `wiki-ingest` の URL 取得に準じ、各章 URL を defuddle skill で clean markdown 化して `.raw/books/<slug>/chapters/ch-NN.md` に保存する(`.raw/articles/` には置かない。書籍は books/ に統一)。目次ページだけ渡された場合は、目次から章 URL リストを組み立ててユーザーに提示・確認してから取得する。既存 SRE Book の原本が `.raw/articles/sre-book-*` にあるのは過去の運用で、**移行しない**。

---

## Step 0.3: 章リストの確定と全章ページ名の事前決定

fan-out の前に、オーケストレータが以下を確定する:

1. **章リスト**: 章番号・章題・ページ範囲(または URL)。20 章超はここでユーザー確認。付録と部の序論・むすびの扱いも同時に決める(寄稿集は source 化、資料リスト・索引は対象外にしてよい。判断が割れたら `AskUserQuestion`)。**章題は `toc.txt` の章行から拾う**(`chapter_NN=` の題は節題に化けうる)。同題の章があるなら、担当 subagent に「あなたの担当は第 II 部のほう」のように明示する。
2. **全章の source ページ名**: `@YYYY__PUBLISHER__BookTitle - Chapter N 題.md` を全章分決める(Navigation 行用)。付録は `Appendix A 題`。章題は原題どおり、勝手に整形しない。
3. **バッチの日付を 1 つに固定する**。日付タグ・`date`・`created`・`updated` はオーケストレータが決め打ちし、全 subagent に同じ値を渡す。
4. **book entity と著者 entity の骨格を先に作る**(書誌と空の「構成と主要テーマ」)。各章の `related` がリンク切れにならず、著者 entity の競合も防ぐ。**編集権はオーケストレータだけ。** 序文由来の記述に章 source を出典として付けない(Step 2)。fan-out 後に出典表記を点検し、報告の「気づいた矛盾」を読み飛ばさない。
5. **印字ノンブルと PDF ページ番号のずれを確認し、briefing に書く。** 出典表記は印字ノンブルに統一する。章一覧には PDF ページ範囲を書き、「引用は節番号、無ければ印字ノンブル」と明示する。
6. **briefing は読み手用と書き手用に分ける**(後述の「並列 fan-out」参照)。3 章以上では同じスクラッチに `briefing-extract.md`・`briefing-write.md` と `extracts/` を作る。
7. **画像の章別振り分け**(Step 0.5 実行後): `images.json` のページ番号、または §1.7 で切り出した図の一覧を章のページ範囲に対応づける。

---

## Step 0.5: 画像の方針

> [!important] 全ページレンダリング(`pdftoppm` 全頁)は**禁止**
> スライドと違い、書籍は数百ページある。ページ画像化はしない。埋め込み画像抽出とページ範囲限定のクロップだけを使う。

図表は `wiki-ingest-paper` Step 0.5 と同じ扱い(コンタクトシート → 取り込み対象の確定 → attachment → 本文近傍埋め込み)。本文が参照している図表は除外理由がない限り全部取り込む。書籍固有は「一括抽出の規模制御」と「章単位のスコープ」だけ。長いクロップ手順・スキャン PDF 警告・フォント判別は [`references/figures.md`](references/figures.md)。

1. **ページ数は実行前に判定する。** `fetch-book.sh` の `pages=` を見て、**300 ページ超なら `extract-paper-images.mjs` を実行しない**(クロップのみへ進む)。300 ページ以下ならオーケストレータが 1 回だけ一括抽出する。100 枚超なら結果を破棄してクロップのみ。
2. **本文参照を全章 1 回 grep**して総点数を把握する。判断基準は点数ではなく「書籍全体に通用する機械的な切り出し規則が立つか」。**規則が立てばオーケストレータが 1 パスで全図を切り出す。** 規則が立たないクロップはオーケストレータか読み手が行う。書き手に章本文を読ませてクロップさせない(判明している判別条件は読み手用 briefing に書く)。10 点以下はオーケストレータ。0 件なら図の工程を省略し、その章には「図の作業は不要」と書く。
3. **コンタクトシートを先に Read**する。判断不能セル・番号未確定セルだけ個別 Read。本文参照分は全件必須。書き手は briefing の図表表とコンタクトシートと extract の「図表」節だけを使う。章 txt からのキャプション抽出は読み手またはオーケストレータ。
4. **除外リスト**(ページ全体スクショ、凡例・装飾・著者近影、ほぼ同一構図の繰り返し、文脈なし断片)以外は全部埋め込む。枚数上限は設けない。
5. **attachment** は `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`。ファイル名に `token`・`secret`・`password`・`credential`・`key` を含めない。
6. **埋め込みは本文近傍。**「## 図表」セクションは作らない。ベクター図で埋め込みが取れない場合は [`references/figures.md`](references/figures.md) のキャプション座標クロップを、自章のページ範囲に限定して適用する。切り出し後は本文 grep の図番号集合と点数照合し、差分ゼロにしてから fan-out する。

```bash
# 300 ページ以下のときだけ
node scripts/extract-paper-images.mjs ".raw/books/<slug>/<slug>.pdf" ".raw/books/<slug>/images"
python3 scripts/contact-sheet.py --manifest ".raw/books/<slug>/images/images.json" \
  --out "$TMPDIR/cs-<slug>" --index-txt
```

---

## 並列 fan-out(3 章以上は読み手と書き手を別起動)

3 章以上は既定で **2 段階**の subagent 並行。プロトコルは汎用 `wiki-ingest` の「Batch Ingest › Parallel mode (subagents)」に従う。本スキル固有の規則:

- **読み手と書き手は必ず別起動。** 同一 subagent に「まず extract を書いてから、章本文を再読せずに書け」と 1 体で回してはならない。ターンをまたいで章本文が常駐文脈に残る。
- **1〜2 章は 1 体可。** 断片 ingest は従来どおり 1 体で読んで書いてよい(分離の固定費のほうが大きい)。extract ファイルは作らなくてよい。
- **読み手(extract):** 章本文を全部読む。[`references/extract-schema.md`](references/extract-schema.md) どおり `<scratch>/extracts/ch-NN.md` を書く。**見出しは空でも残す(schema)。** wiki ページは書かない。git 禁止。catalog / book entity 禁止。完了報告は extract パスと一行要約と図表点数だけ。
- **extract の置き場:** 読み手用 briefing と同じスクラッチ。例: `/tmp/book-<slug>/extracts/ch-NN.md`。**extract を `.raw` に置かない**(wiki にも置かない。git 管理しない)。
- **briefing は読み手用と書き手用に分ける。** `<scratch>/briefing-extract.md` と `<scratch>/briefing-write.md` の 2 ファイル。読み手は書き手用を読まない。1 ファイルにまとめるなら明確な 2 節にし、「読み手は書き手節を読むな」と書く。
- **読み手用 briefing:** extract-schema、置き場、git 禁止、wiki 禁止、完了報告(パス・一行要約・図表点数)。`wiki-page-write` / 本文テンプレ / concept 追記手順は**入れない**。
- **書き手用 briefing:** ヘルパー、テンプレ、extract パス、全章ページ名、既存 concept 候補、短い章は 30〜50 行。**章本文の原本パスは書かない**(必要なら「locator 確認は grep のみ。パスはオーケストレータが指定した 1 ファイルの該当行だけ」)。
- **検査:** 書き手起動前に対象章の extract を 1 回かける。`python3 scripts/wiki-extract-check.py --forbid-raw extracts/*.md`(スクラッチの `extracts/` で)。`MISSING` / `QUOTE-HEAVY` / `FORBID-RAW`・読み手失敗の章は書き手を起動せず、読み手をやり直す。
- **書き手(write):** extract と [`references/subagent-brief.md`](references/subagent-brief.md) の書き手節と書き手用 briefing を読む。**章本文の全文 Read はしない。** locator の確認は grep のみ。extract が薄いときのピンポイント Read は **1 章あたり最大 2 箇所、各 80 行**。超えるなら読み手やり直し。`wiki-page-write.py` / `wiki-append.py` で source / entity / concept を書く。画像は書き手用 briefing の図表表 + コンタクトシート + extract の「図表」節。
- **subagent の個別プロンプトにはフル SKILL を渡さない。** 読み手には extract-schema と brief の読み手節と `briefing-extract.md`。書き手には brief の書き手節と `briefing-write.md`。
- **git 禁止を両方の briefing に書く。** `git add` / `git commit` / `git stash` / `git checkout` 禁止。確認の `git status` / `git diff` のみ。
- **3〜4 体をローリングで回す**(ウェーブではない)。読み手を先に回し、検査を通った章から書き手を投入する。同時実行は読み手と書き手を合わせて 3〜4 体。
- **同じハブ concept を奪い合う章の直列化は書き手に適用する。** 読み手は wiki を書かないので並行してよい。空いた枠には concept の重ならない章の書き手を入れる。後発の書き手には先行章が触った concept のファイル名を伝える。
- **book entity と共有カタログはオーケストレータのみ。** 読み手も書き手も `wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`・`overview.md`・`.raw/.manifest.json`・book entity を書かない。
- 章の中身の示唆は原本 grep で裏を取ってから書く。書き手の報告は `git status` で照合する。log と commit はバッチ全体で 1 回。

詳細と事例は [`references/fan-out.md`](references/fan-out.md)。

---

## Step 1: 章 source ページを作る(`wiki/sources/`)

3 章以上では書き手が extract と書き手用 briefing から組む。章本文の全文 Read はしない。ピンポイント Read は 1 章あたり最大 2 箇所、各 80 行。超えるなら読み手やり直し。**1〜2 章は 1 体可**で、そのときは従来どおり自章テキストを全部読んでから書く(斜め読みしない。長い章は分割して読む)。

### ファイル名(`@` プレフィックス必須)

```
wiki/sources/@YYYY__PUBLISHER__BookTitle - Chapter N 題.md
```

例: `@2016__OReilly__SRE Book - Chapter 12 Effective Troubleshooting.md`。`PUBLISHER` は出版社の通称(`OReilly`・`Wiley` 等。日本語版オライリーは `OReillyJapan`)。`@` の理由と参照リンク規約は conventions §4 のとおり(source を参照する wiki 内リンクは全て `@` 付き)。禁則文字(`:` `/`)は前後を空白で囲んで置換。

付録は `Chapter N` の代わりに `Appendix A` を使う(`@2024__OReillyJapan__SREをはじめよう - Appendix A 若きSREへの手紙.md`)。`title` も同様に `"BookTitle - Appendix A: 題"` とする。

### frontmatter(conventions §2/§3 準拠)

```yaml
---
address: c-NNNNNN            # wiki-page-write.py が採番して挿入する(新規ページのみ)
type: source
title: "BookTitle - Chapter N: 章題"
date: 2026-08-11 10:00
created: 2026-08-11
updated: 2026-08-11
aliases: []
tags:
  - 2026/08/11               # 日付タグ先頭(必須)
  - source
  - <domain-tag>
status: developing
publish: false               # 必須: 著作権コンテンツのため Obsidian Publish から除外
related:
  - "[[<書名>]]"             # book entity(必須)
sources:
  - "[[.raw/books/<slug>/chapters/ch-NN.txt]]"
source_type: book
author: "章著者(不明なら書籍の著者・編者)"
date_published: YYYY-MM-DD   # 書籍の出版日
url: ""                      # 章 URL(形態 b)。形態 a は空可
confidence: high
key_claims:
  - "章の中心主張(本文に遡及可能なもの)を 3〜5 件"
---
```

### 本文テンプレート(書籍章向け。paper の 9 セクションは使わない)

```markdown
# BookTitle - Chapter N: 章題

> 前: [[@...Chapter N-1...]] | 次: [[@...Chapter N+1...]] | 書籍: [[<書名>]]

## 要約

章全体の要約を 5〜10 行(常体)。

## 主要概念

- **用語(原語)**: 定義。章内の位置づけ。
- (章の核となる概念・用語を箇条書きで)

## 主要主張

- 主張。(ch.N §N.M)
- (各主張に章・節番号またはページ番号 p.NNN の位置情報を付す。`(Source: …)` 表記は使わない)

## 実践的指針

- (実務書のみ。該当なければセクションごと省略)

## 関連

- 概念: [[...]] / 実体: [[...]] / 関連章: [[@...]]

## 出典

- 著者, *BookTitle*, 出版社, 年, Chapter N.
```

- Navigation 行: 先頭章は「前:」を、末尾章は「次:」を省略。断片入力(c)は前後章ページが存在しないため「書籍: [[<書名>]]」のみとする。
- 分量は 100〜300 行上限(conventions §7)、実際は 60〜120 行を想定。概念の深掘りは concept ページへ逃がす。
- **出典に無いことは書かない**。訓練データの知識・推測で補完しない。各主張は章本文に遡及できること。
- 出典検査は `wiki-ingest-paper` と同じ規律(結果はチャットに報告し、ノート本文に「検証パス」節を書かない)。

---

## Step 2: book entity ページ(必須)と、その他 entity

### book entity(書籍ハブ)

書籍そのものを表す entity を `wiki/entities/<書名>.md`(原名)に 1 つ作る。**`entity_type: book`**(conventions §3)。**book entity(ハブ)は最初から `entity_tier: full`**。`first_mentioned` には最初に作成した章 source を入れる。

```yaml
---
address: c-NNNNNN
type: entity
title: "<書名>"
date: 2026-08-11 10:00
created: 2026-08-11
updated: 2026-08-11
aliases: []
tags:
  - 2026/08/11
  - entity
  - <domain-tag>
entity_type: book
role: "書籍の一言での位置づけ"
first_mentioned: "[[@YYYY__PUBLISHER__BookTitle - Chapter 1 ...]]"
status: developing
related: []
---
```

本文テンプレート(既存 `wiki/entities/SRE Book.md` を正式化したもの):

```markdown
# <書名>

## 概要

書籍が何であり、なぜ重要かを 2〜3 文。

## 書誌情報

- 出版社 / 出版日 / 著者・編者 / 構成(全 N 章・Part 構成)/ ISBN / URL

## 構成と主要テーマ

### Part I: ...(第 1〜M 章)
Part の主題を 1〜2 文。
→ [[@...Chapter 1 ...]] — 章の一行要約
→ [[@...Chapter 2 ...]] — 章の一行要約

## 影響と位置づけ

## 関連

## 出典
```

- 「構成と主要テーマ」の各章の一行要約は、fan-out した各 subagent の報告から**オーケストレータが**組み立てる。
- **取り込んだ章だけを列挙する**(範囲指定・断片入力で未取り込みの章は列挙しない。目次だけからの水増しをしない)。
- 章数が多く 300 行を超えそうな場合は、章の一行要約を Part 単位の要約に縮約し、章リンクは列挙のみとする。
- **断片入力(c)のとき**: 既存の book entity があれば「構成と主要テーマ」に `→ [[@章ページ]] — 一行要約` を追記する(`wiki-append.py` の `section_append` で 1 回。ロック・`updated`・日付タグはヘルパーが扱う。再採番しない)。なければ判明している書誌情報のみで新規作成する(`status: seed`)。

既存の `SRE Book.md` / `SRE Workbook.md`(`entity_type: product`)は**遡及変更しない**。新規の書籍 entity から `book` を使う。

### 序文由来の記述の出典表記

序文・前書き・献辞を source ページ化しない方針にした場合(Step 0.3 で決める)、そこから採った記述の出典に**章 source ページへのリンクを使ってはならない**。著者の経歴、講義シリーズとしての成立事情、章の由来、謝辞、献辞はほぼ全て序文にしかなく、章本文には現れない。章ページを指すと、その章を読んだ subagent や後の `wiki-lint` から「出典に無い記述」として突き返される。

書誌そのものを指す形に統一する:

```markdown
Simon は同書第 2 版序文で「私の主張のうち彼が同意しない部分はおそらく誤りだが……」と書いている。([[<書名>]] 第 2 版序文)
```

- 複数の記述がまとめて序文由来なら、節の冒頭で一度だけ断ってもよい(「以下の経歴はいずれも第 2 版序文・第 3 版序文を出典とする」)。
- `first_mentioned` と `sources:` は別問題で、**その人物・組織が章本文にも登場するなら章ページを指してよい**。1 回 grep すれば確定する: `for f in .raw/books/<slug>/chapters/ch-*.txt; do printf "%s: " "$(basename $f)"; grep -c "<名前>" "$f"; done`
- 序文を source 化した場合はこの制約は無く、通常どおり `@...Preface ...` ページを指す。

### その他 entity

書籍・章に登場する人(著者・編者)・組織(出版社等)・製品/システム・データセットは `wiki-ingest-paper` Step 2 と同じ規約で作成・更新する(原名ファイル名、`entity_type`、`first_mentioned` は `@` 付き、address 採番)。既存の有無は `wiki-resolve.py entity:"<名1>" entity:"<名2>" ... --compact` で**登場する全 entity 名を 1 回の呼び出しで**確認する。**共著者・一度きりの言及は `entity_tier: stub`**(frontmatter + 所属/役割 2〜3 行。excerpt 不要)。著者 entity の `sources:` には代表章(執筆章が分かるならその章)を積み増す。

---

## Step 3: concept ページ(`wiki/concepts/`)

章が扱う重要な手法・枠組み・問題ドメインを `wiki/concepts/<原名>.md` に育てる。既存 concept の有無は index ではなく resolve で確認し、候補語はまとめて 1 回で解決する。

```bash
python3 scripts/wiki-resolve.py concept:"<候補1>" concept:"<候補2>" concept:"<候補3>" --compact
```

**1 書籍バッチ(文書全体)で concept は新規最大 3、更新最大 5。** 章ごとにリセットしない。溢れた候補は Step 4 の log に `Deferred:` 行として残す。台帳にも積む: `python3 scripts/concept-candidates.py add --name <候補> --source "[[@<今回の source>]]" --reason "上限超過"`。`wiki-resolve.py` が `ledger:<名>(<k> docs, pending)` を返した候補は既に保留中なので同じコマンドで言及を足し、`ready`(2 文書以上)なら今回の新規枠で優先して新設し `promote` する(conventions §12 ルール 5)。新設か保留かで迷う候補は `python3 scripts/wiki-profile.py`(研究関心の要約 25 行、profile 全文は読まない。終了 3 なら関心判定を飛ばす)に照らす。対象外の用語は concept にせず source に留め、コア関心の候補は 2 文書目で優先して新設する(conventions §12 ルール 3)。既存 concept を更新するときは全文を `Read` せず、触る concept を並べた `wiki-excerpt.py P1 P2 --tail 15 --budget-tokens 1800`(必要なら `--outline`)で必要節だけ読む。`--total-budget` を使うなら `N * --budget-tokens` 以上にする。更新上限 5 なら 9000 以上。切れたページは読んだことにしない。

規約は conventions §8 と §12 のとおり: concept の本文は主題別の節(再編纂が書く。**主題節を書き換えない**)で、ingest の追記先は `## 未編纂の観察`(**2 つ以上のソースの突き合わせで見えた観察だけ**。旧名 `## 横断的知見` が残るページではそこ)と `## 未解決の問い` の 2 節。**上限内で触れた** concept のこの 2 節を更新する。追記の前に `wiki-excerpt.py --outline` で既存の命題を見て、補強・反証する観察なら冒頭に `[節名]` を付ける。積み増し原則(ingest は既存項目を書き換えない)。矛盾は `> [!contradiction]` callout を両ページに。

同一書籍の複数章は独立ソース扱いでよい(章 A と章 B の突き合わせで見えた観察も受信箱に書ける。その場合も `(Source: [[@章A]], [[@章B]])` を明記)。書籍 1 冊で concept を新設し、その時点で複数章の突き合わせが揃っているなら、主題節を最初から立ててよい(見出しは主題の名詞。「知見」「観察」「考察」「まとめ」「横断的」を使わない)。

---

## Step 3.5: fan-out 後の機械的検証(オーケストレータ)

索引を書く前に**実体を機械的に検査する**。subagent の「全点埋め込んだ」という報告は照合しないと信用できない。検査は 1 コマンドに束ねる(Bash を何度も撃つと、その出力がそのまま常駐文脈に積まれる)。

```bash
cd <vault ルート>
python3 scripts/wiki-verify-ingest.py --glob 'wiki/sources/@YYYY__PUB__<書名>*' \
  --attachments-slug <slug> --expect-count N --date YYYY-MM-DD \
  --require-related '[[<書名>]]'
```

- **図の埋め込みと attachment を両方向で照合する**。未解決の埋め込みは `EMBED`、参照されていない attachment は `ATTACH-UNUSED` として出る。summary の `embeds=` が §1.5 の grep 総点数と一致し、`unresolved_embeds` と `unused_attachments` が 0 なら図の工程は完了である。
- **Navigation 行のリンク**は `NAV-LINK` として本文リンク(`LINK`)と別枠で出る。前後章ページ名の 1 文字違いはここに出る。
- **frontmatter の必須項目**(`address`・`publish: false`・`--date` の日付タグ・`source_type`・`title`/`date`/`created`/`updated`/`status`)と H1 を全章分見る。`--require-related` は各章の `related` が book entity を指すことを `FM-RELATED` で見る。
- **枚数**は `--expect-count` が Step 0.3 で確定した枚数と照合し、合わなければ `COUNT` で落ちる。ほかに自己参照・分量・重複 address・source 本文の検証結果節も同時に見る。
- error があれば終了コード 1 なので、その場で直してから Step 4 へ進む。所見が多いときは `--quiet`(error のみ)、機械処理するなら `--json`。

---

## Step 4: 索引・ホット・ログ・manifest の更新(オーケストレータのみ)

全 subagent 完了後、オーケストレータが 1 パスで更新する。

> [!important] 索引更新の前に必ず `git status --porcelain=v1 -- wiki/ .raw/` で実体を確認する
> subagent の報告は「次にやります」で終わっていて実際には未実行、または既存ページを新規作成と混同している(逆も同様)ことがある。索引・hot・log・manifest に載せる「作成/更新したページ一覧」は、subagent の報告文をそのまま転記せず、`git status`(新規は `??`、更新は ` M`)と各ページの `git diff` で実際の差分を確認したうえで確定する。報告に無いが `git status` に出てくる新規/更新ページ(subagent が自発的に作った entity/concept 等)も漏らさず載せる。

> [!warning] ページ一覧は `git status` の出力から機械的に組み立てる。目で見て手で書き写さない
> `git status -- wiki/` には今回と無関係な変更や他セッションの並行 ingest が混ざる。目で選り分けず、差分の中身で機械的に分類する。件数を数えて `git status` の件数と一致することを確認する。漏れた側(`CLASSIFY-OTHER`)は必ず目視する。共有索引は `--classify` の `CATALOG` 行で自分の追記行数と照合する。相手のコミットへの巻き込みは `git show --name-only <sha>` で見る。逸話は [`references/fan-out.md`](references/fan-out.md)。
> ```bash
> cd <vault ルート>
> python3 scripts/wiki-verify-ingest.py --match "<書名>" --classify --json > "$TMPDIR/book_verify.json"
> python3 -c "import json;print('\n'.join(json.load(open('$TMPDIR/book_verify.json'))['targets']))" \
>   > "$TMPDIR/book_files.txt"; wc -l < "$TMPDIR/book_files.txt"
> ```

> [!note] 索引ファイルの frontmatter も更新する
> `wiki/index.md` と各 `_index.md` の `updated` / 日付タグ、および `wiki/hot.md` / `wiki/log.md` への追記は **`wiki-catalog.py` 経由**で扱う(Read/Edit しない)。

1. **カタログファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)を Read/Edit しない。** `wiki-catalog.py` で追記する(自己 lock。カタログに `wiki-lock.sh` を二重に取らない):
   ```bash
   python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
   ## [YYYY-MM-DD] ingest-book | <書名>
   - Source: `.raw/books/<slug>/`(N 章 / 入力: pdf|web|fragment)
   - Book entity: [[<書名>]]
   - Chapters: [[@...Chapter 1...]]〜[[@...Chapter N...]](N 件)
   - Pages created: (entity・concept を列挙)
   - Pages updated: (更新した既存ページを列挙)
   - Key insight: 書籍全体から新しく分かったことを数行で。
   - Deferred: [[候補 concept]] — 今回の上限超過。次の関連ソースで。
   EOF
   )"

   python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
   ## YYYY-MM-DD | ingest-book | <書名>
   - Focus: [[<書名>]] + 代表章 2〜3 件の要点(全章は列挙しない)
   - Key insight: 一文。
   - New: [[...]]
   - Updated: [[...]]
   EOF
   )"

   python3 scripts/wiki-catalog.py prepend-changelog \
     --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest-book | <書名>\n- 新規 concept: [[A]]\n"

   python3 scripts/wiki-catalog.py add-catalog-line \
     --file wiki/concepts/_index.md \
     --section "現行コンセプトカタログ" \
     --line "- [[A]]"

   python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest-book | <書名>\n- Source: [[@...]]\n"
   ```
   新規 source/entity/concept ごとに、該当 `_index.md` へ `prepend-changelog` + `add-catalog-line`(必要な section を指定)。
2. 大局が変わったら `wiki/overview.md`(overview は通常サイズのため Read/Edit 可)。
3. `.raw/.manifest.json` に記録。**キーはディレクトリ** `.raw/books/<slug>`(video の前例に準拠):
   ```json
   ".raw/books/<slug>": {
     "hash": "<PDF の md5。形態 b は代表章の md5 か 'web-chapters'>",
     "ingested_at": "YYYY-MM-DD",
     "chapters_count": 17,
     "chapter_pages": ["wiki/sources/@....md", "..."],
     "pages_created": ["..."],
     "pages_updated": ["..."]
   }
   ```
   増分章追加(断片・範囲追加)のときは**同キーを更新して章を積み増す**。

4. **BM25 索引の差分更新**(commit 前に必須):
   ```bash
   python3 scripts/wiki-retrieve-refresh.py \
     --pages wiki/sources/@... wiki/entities/<書名>.md wiki/concepts/... \
     --no-llm
   ```
   `--pages` には今回作成・更新した wiki ページパスを列挙する。

---

## 再取り込み判定と増分章追加

- `.raw/.manifest.json` の `.raw/books/<slug>` キーを確認。PDF ハッシュ一致かつ対象章が全て `chapter_pages` にあれば「取り込み済み(未変更)。force で再取り込み」と報告してスキップ。
- **同じ書籍の未取り込み章の追加**は再取り込みではない: 既存の章 source と book entity を温存し、新しい章 source を作って book entity の「構成と主要テーマ」へ追記、manifest の同キーへ積み増す。
- `fetch-book.sh` は既存 `.raw/books/<slug>/` にファイルがあると停止する(物理ゲート)。増分追加で原本が既にある場合は再取得せず、既存の `chapters/ch-NN.txt` を読む。

---

## Step 5: git commit(バッチ全体で 1 回)

`wiki-ingest-paper` Step 5 と同じ方針: ユーザーの明示指示を待たず自動コミットしてよい(1 書籍バッチ = 1 まとまり)。今回の ingest で作成・更新した具体ファイルだけを stage し、`git add wiki/ .raw/` のような広い指定は使わない。共有メタファイルは stage 前に `git status --porcelain=v1 -- <path>` と `git diff -- <path>` で自分の差分だけか確認する。

```bash
git add \
  ".raw/books/<slug>/" \
  ".raw/.manifest.json" \
  "wiki/sources/@YYYY__PUBLISHER__BookTitle - Chapter"*.md \
  "wiki/sources/_attachments/<slug>/" \
  "wiki/entities/<書名>.md" \
  "wiki/entities/<updated-entity>.md" \
  "wiki/concepts/<updated-concept>.md" \
  "wiki/sources/_index.md" "wiki/entities/_index.md" "wiki/concepts/_index.md" \
  "wiki/index.md" "wiki/hot.md" "wiki/log.md"
git commit -m "wiki: ingest-book | <書名>"
```

更新した entity / concept は数十件になるので、上のように手で並べず **Step 4 で作った本書ファイル一覧(`$TMPDIR/book_files.txt`)をそのまま渡す**:

```bash
tr '\n' '\0' < "$TMPDIR/book_files.txt" | xargs -0 git add --
```

- **stage したらコミット前に厳密照合する**。ファイル名だけを目で眺めても、entity/concept は書名を含まないので判断できない。`git diff --cached --name-only` の集合が「本書ファイル一覧 + 共有索引 + `.raw/books/<slug>/` + attachment ディレクトリ」と過不足なく一致することを 1 コマンドで確かめる(末尾 `/` はディレクトリ前方一致):
  ```bash
  python3 scripts/wiki-verify-ingest.py --staged-check \
    --expect-files "$TMPDIR/book_files.txt" \
    --extra-files ".raw/books/<slug>/" "wiki/sources/_attachments/<slug>/" \
    ".raw/.manifest.json" wiki/index.md wiki/hot.md wiki/log.md \
    wiki/sources/_index.md wiki/entities/_index.md wiki/concepts/_index.md
  ```
  `STAGED-EXTRA`(未検証の staged ファイル)と `STAGED-MISS`(期待集合のうち未 staged)がともに 0 件、つまり終了コード 0 を確かめてから commit する。
- **subagent が独断でコミットしていた場合**は、そのコミットに他章や他セッションのファイルが巻き込まれていないかを `git show --name-only <sha>` で確認する。巻き込みがなければ履歴が 2 つに割れるだけなので巻き戻さず、log の備考に「N 章分は先行コミット `<sha>` に含まれる」と書いて残す。**巻き込みがあった場合はユーザーに報告して判断を仰ぐ**(勝手に履歴を書き換えない)。
- 先行コミット済みの章のファイルは `git status` に出てこない。**Step 4 のページ一覧を組み立てるときは、そのコミットの `git show --name-only` を合流させる**(合流させないと索引・log から丸ごと落ちる)。

コミット後に `python3 scripts/usage-report.py --self --log-line` を実行し、出力の 1 行を**チャットの完了報告に含める**(`wiki/log.md` には書かない。log エントリはセッション終了前に書かれるため数値が確定しない)。`--self` は `CLAUDE_CODE_SESSION_ID`(Claude Code)、`CODEX_THREAD_ID`(Codex。無ければ `CODEX_SESSION_ID`)、`CURSOR_CONVERSATION_ID`(Cursor)をこの順で見る。環境変数が空なら `--session <このセッションの ID>`。Cursor / Codex 経由でも `--latest` には落とさない。Cursor の数値は transcript と composer スナップショットからの概算で、行末が `Cursor概算` になる。Codex のトークンは `~/.codex/sessions/**/rollout-*.jsonl` の `token_usage_record` の実測で、行末が `Codex定価目安` になる。Codex Plus の実請求ではなく、Claude / Cursor の数値と混ぜて前後比較しない。振り返りには `--since YYYY-MM-DD` を使う。章ごとに subagent へ委譲した場合、subagent 側の使用量は親ログに含まれないため、この値はオーケストレータ側だけのものになる。

---

## やってはいけないこと

- **`scripts/allocate-address.sh` を直接実行しない。** 採番はヘルパー外では失敗する。`wiki-page-write.py` がロック内で行う。`cd` だけの Bash も禁止(必ず `cd <vault絶対パス> && <実処理>`)。
- **新規 source / entity / concept を `Write` ツールで作らない。** 既存なら append。衝突したらマージ復旧せず止める。
- **シートで番号が付いた attachment を Read しない。** `individual-reads` はシートを除く画像 Read の実数。
- **完了報告の計測に `--latest` を使わない。** `--self`(Claude Code / Codex / Cursor の環境変数。失敗時だけ `--session <ID>`)にする。Cursor や Codex 経由を理由にスキップしない。
- **章 source ページの `publish: false` を省略しない**(著作権コンテンツの外部公開防止)。
- **書籍全体を 1 つの source ページに押し込まない**(断片入力を除く)。逆に断片入力を勝手に「全体 ingest」へ拡大しない。
- **全ページレンダリング(`pdftoppm` 全頁)をしない**。書籍は数百ページある。
- **subagent から book entity・共有ファイルを書かない**(オーケストレータのみ)。
- `.raw/` の既存ファイルを変更しない(本スキルが新規生成する `.raw/books/<slug>/` 配下と `.raw/.manifest.json` は対象外)。
- `papers/`・`research/`・`structures/`・`notes/` を書き換えない。wiki から一方向リンクのみ。
- 目次だけから未読の章の要約を書かない(読んだ章だけを wiki 化する)。
- 重複ページを作らない。作成前に `wiki-resolve.py` で既存(書籍 entity・章 source・concept)を確認(index 全文や grep 全走査に頼らない)。
- **ページ数を確認する前に画像一括抽出を実行しない**。`fetch-book.sh` の `pages=` 出力を見て 300 ページ超なら実行自体をスキップする(実行してタイムアウトしてから諭さない)。
- **subagent の「これから更新する」という発言を実施済みとして扱わない**。索引・log・manifest に書く前に必ず `git status`/`git diff` で実体を確認する。
- **章検出結果の `chapters_count` が想定と食い違う場合、確認せずそのまま fan-out に進まない**。多すぎる場合は節見出しの誤検出、少なすぎる場合は Part 単位への化けを疑う。`toc.txt` の `outline|` 行(level 1 と 2 の両方)と本の目次を突き合わせ、必要なら `--chapters` で正しい章境界を再検出してから進む。
- **`chapter_NN=` の題を鵜呑みにしない**。`--chapters` で手動指定したとき、題は指定した開始ページの outline エントリから引かれるため、章扉と同じページに節見出しがあると「1.1 SREとは何か」のような**節題が章題として返る**。章題は `toc.txt` の level 2 エントリから自分で拾って確定する。
- **同じ一覧を複数ファイルへ手で書き写さない**(索引・log・manifest のページ一覧)。`git status` の出力から機械的に生成し、件数を照合する。
- **図の点数を調べずに図の工程を subagent へ丸投げしない**。本文の図参照を 1 回 grep すれば総点数が分かる。**機械的な切り出し規則が立つなら、点数によらずオーケストレータが 1 パスで片付けたほうが速く確実である**。
- **「図N-M」で始まる行をキャプションとみなさない**。本文中の図参照が同じ書き出しを持つ。洋書は末尾のコロン(`Figure 4.1:`)で分離できることが多いので先に試し、それで駄目ならフォント・サイズで判別する([`references/figures.md`](references/figures.md) §1.7)。
- **章の中身の示唆を裏取りせずに個別プロンプトへ書かない**。原本を 1 回 grep すれば確定する。巻末一覧の印字ページから章を逆算しない([`references/fan-out.md`](references/fan-out.md))。
- **切り出せた図の点数を照合せずに fan-out しない**。本文 grep の図番号集合と突き合わせ、差分ゼロにしてから配る。未マッチを黙って残すと、その図は永久に埋め込まれない。
- **outline の level だけで章を切り出せると仮定しない**。部・章・節が同じ level に同居する書籍がある。章題のパターンで絞る(Step 0)。
- **アウトラインが 0 件だからといって取り込みを諦めない**。全文テキストの目次とランニングヘッダの印字ノンブルから、オフセット 1 個で章境界が復元できる(Step 0)。
- **矩形を確かめずに `get_image_rects()` の結果を図として扱わない**。スキャン PDF ではページ全面の矩形が返り、全ページが 1 枚の巨大な図として切り出される。ページ矩形と比べて一致していたらキャプション座標クロップへ切り替える([`references/figures.md`](references/figures.md) §1.7)。
- **キャプション座標クロップの結果を目視せずに配らない**。スキャン PDF では上端・下端が切れやすい。1 点ずつ Read で開いて境界を詰める([`references/figures.md`](references/figures.md) §1.7)。
- **序文由来の記述に章 source ページを出典として付けない**。著者の経歴・成立事情・献辞は章本文に無い。序文を source 化しないなら書誌そのものを指す(Step 2)。
- **先に作った book entity・著者 entity を fan-out 後に点検せずに済ませない**。骨格段階で書いた出典は序文由来のことが多く、誤りは subagent の報告からしか見つからない(Step 0.3)。
- **読み手と書き手を同一 subagent で回さない**(3 章以上)。別起動する。1〜2 章は 1 体可。
- **extract を `.raw` に置かない**。wiki にも置かない。briefing と同じスクラッチの `extracts/` だけ。
- **読み手に wiki を書かせない**。`MISSING` / `QUOTE-HEAVY` / `FORBID-RAW` の extract では書き手を起動せず、読み手をやり直す。書き手起動前に `python3 scripts/wiki-extract-check.py --forbid-raw extracts/*.md` を 1 回かける。
- **読み手用 briefing に `wiki-page-write` / 本文テンプレ / concept 追記を入れない。** 書き手用 briefing に章本文の原本パスを書かない。
- **subagent に git 操作を許したまま fan-out しない**。briefing に明示的な禁止条項を書く。書かなければコミットされる。
- **別セッションの並行 ingest を想定せずに `git add` しない**。差分の中身で本書分を機械的に分類し、**分類から漏れた側を 1 件ずつ確認する**(Step 4)。
- **索引を書く前の機械的検証(Step 3.5)を飛ばさない**。埋め込みの解決・未使用 attachment・Navigation リンク・frontmatter 必須項目は、報告文ではなく `wiki-verify-ingest.py` で確かめる。検査のために手書きの bash ループを組み直さない(呼び出しも出力もそのまま常駐文脈に積まれる)。
