---
name: wiki-ingest-paper
description: "Paper-specialized wiki ingest. Downloads the paper PDF as an immutable original into `.raw/papers/`, then files the paper into the cross-source wiki layer (`wiki/sources/`, `wiki/entities/`, `wiki/concepts/`) with a create-paper-note-depth Japanese memo. Use this — not the generic wiki-ingest — whenever the source is a research paper: an arXiv URL/ID, a paper PDF (URL or local), a DOI, an OpenReview/ACL/USENIX link, or a paper title the user wants in the wiki. Triggers on: 'ingest this paper', 'この論文を wiki に', 'arxiv を ingest', 'PDF を取り込んで wiki 化', 'ingest https://arxiv.org/...'. The generic wiki-ingest only fetches HTML text and never downloads the PDF; reach for THIS skill when a real PDF should land in `.raw/papers/`. Distinct from create-paper-note, which writes a single-source detailed note into `papers/` — this skill instead builds cross-source wiki pages and never writes to `papers/`. When 3+ papers are given at once, this skill fans out subagents and ingests them in parallel by default — no need for the user to ask for '並行処理'."
---

# wiki-ingest-paper: 論文特化の wiki 取り込み

論文を **PDF 原本ごと** wiki レイヤーに取り込む。`wiki-ingest` の横断集約フロー(source/entity/concept・index/hot/log・lock・mode router・manifest)に、`create-paper-note` 由来の論文向け深掘り(abstract 翻訳・問題設定/提案手法/新規性/実験/結果/考察・図表)を載せたものだ。

このスキルが汎用 `wiki-ingest` と違う一点: **PDF をダウンロードして `.raw/papers/` に不変原本として置く**。汎用版は URL の HTML テキストを `.raw/articles/` に保存するだけで、PDF バイナリは取得しない。論文を扱うなら必ずこちらを使う。

`create-paper-note` とも役割が違う。あちらは単一ソースの詳細メモを `papers/` に書く。こちらは**複数ソース横断の定義・関係・矛盾**を `wiki/` に積み上げる。**このスキルは `papers/` を一切書き換えない。** 既存 `papers/` ノートがあれば wiki から一方向リンクで参照するだけにとどめる。

> [!important] 最初に必ず読むこと
> wiki を操作する前に [`wiki/meta/conventions.md`](../../wiki/meta/conventions.md) と [`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md) を読む。frontmatter・命名・言語(日本語常体)・出典規約は**標準形式より conventions が優先**される。本スキルの記述と conventions が食い違ったら conventions に従う。
>
> **カタログ類は Read しない**: `wiki/index.md`・`wiki/{sources,entities,concepts}/_index.md`・`wiki/hot.md`・`wiki/log.md` の全文を `Read` してはならない。既存ページの発見は `wiki-resolve.py`、カタログ更新は `wiki-catalog.py`、本文の部分読みは `wiki-excerpt.py` に任せる。
>
> 1 論文担当 subagent へ渡す手順は [`references/subagent-brief.md`](references/subagent-brief.md) が正本である。フル SKILL を subagent に渡さない。ベクター図のクロップ手順は [`references/figures.md`](references/figures.md)。

---

## いつ使うか(兄弟スキルとの境界)

| 入力・意図 | 使うスキル | 出力 |
|---|---|---|
| 論文を**横断知識として** wiki に集約(PDF も残す) | **wiki-ingest-paper(本スキル)** | `.raw/papers/*.pdf` + `wiki/{sources,entities,concepts}/` |
| 論文の**単一ソース詳細メモ**が欲しい | `create-paper-note` | `papers/YYYY__PUB__Title.md` |
| 論文以外(技術記事・ブログ等)の URL/テキストを wiki 化 | `wiki-ingest` | `.raw/articles/` + `wiki/` |
| 会議トークの聴講ノート | `create-conference-note` | `research/conferences/` |

判断基準: **入力が論文 かつ 出力先が wiki なら本スキル**。単一ソースの読み込みメモが主目的なら `create-paper-note`。両者は共存し、wiki から `papers/`・`structures/*.MOC.md` へは一方向参照でつなぐ。

> [!note] 論文に発表スライドが付随する場合
> 「論文 + その発表スライド」をまとめて 1 つの source ページに取り込みたいときは、本スキルの中で完結させる(§ Step 0.6 参照)。`wiki-ingest-slides` を別途呼ぶのは、**スライド単体**(論文が無い、または論文とは独立に扱いたい)を取り込むときだけ。

---

## 複数論文のバッチ並行 ingest

論文が3本以上渡されたら、ユーザーが並行処理を指示しなくても**既定で subagent 並行モード**で処理する。プロトコルは汎用 `wiki-ingest` スキルの「Batch Ingest › Parallel mode」節に従う。本スキル固有の要点:

- subagent 1体につき論文1本。各 subagent が Step 0(PDF取得)〜Step 3(concept) までを担当し、wiki ページ書き込みは `wiki-page-write.py`(新規)/ `wiki-append.py`(既存)を通す(ロックはヘルパーの内側)。**論文 1 本は読み手と書き手に分離しない**(本文が短く、分離の固定費のほうが大きい)。1 体が読んで書く。
- 共有ファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`・`overview.md`・`.raw/.manifest.json`)は subagent からは触らない。オーケストレータが全 subagent 完了後に `wiki-catalog.py`(prepend-log / prepend-hot / prepend-changelog / add-catalog-line / prepend-master)で一括更新する(カタログファイルを Read/Edit しない)。各 subagent は `wiki-page-write.py` 経由で自分の新規ページ分を採番してよい(直列化不要)。`scripts/allocate-address.sh` は直接実行しない(ヘルパー外の採番は失敗する)。同じ人物・組織の entity は書き込み直前に resolve し、既存なら append。衝突したらマージ復旧せず止める。
- PDF ダウンロードと PyMuPDF/画像処理は重い。同時実行は 3〜4 体までに抑える
- log エントリは論文ごとに1つずつ(`## [YYYY-MM-DD] ingest-paper | タイトル`)、commit は最後に1回
- **subagent の個別プロンプトにはフル SKILL を渡さない。** [`references/subagent-brief.md`](references/subagent-brief.md) を briefing に含めるか、そこに書いてある手順だけを渡す。

---

## 取り込み前のセットアップ確認

汎用 `wiki-ingest` と同じ前提。要点だけ:

- **書き込みの正はヘルパー**(`wiki-page-write.py` / `wiki-append.py`)。**ヘルパー呼び出しを `wiki-lock.sh` で包まない**(自己デッドロック)。新規 source / entity / concept を `Write` ツールで作らない。`Edit` はヘルパーで表現できない 1 箇所修正だけ。`wiki-catalog.py` は自己 lock するので包まない。
- 新規は `wiki-page-write.py --batch`(採番・検証・lock を内包)。既存追記は `wiki-append.py --batch`。**1 ページに複数回 Edit を撃たない。**
- ファイル名は conventions が優先。`scripts/` は vault ルート直下。manifest は `.raw/.manifest.json`。

---

## Step 0: PDF を取得して `.raw/papers/` に置く(本スキルの核)

論文の参照を解決し、PDF を `.raw/papers/` にダウンロードしてからテキストを抽出する。この一連は決定論的なので、**ヘルパー `scripts/fetch-paper-pdf.sh` に任せる**(毎回手書きしない)。

```bash
# arXiv URL / ID / 任意の PDF URL / ローカル PDF パスのいずれでも可
bash scripts/fetch-paper-pdf.sh "https://arxiv.org/abs/2501.01234"
# 例の出力(key=value):
#   pdf=.raw/papers/arxiv-2501.01234.pdf
#   txt=.raw/papers/arxiv-2501.01234.txt
#   arxiv_id=2501.01234
#   arxiv_html=https://arxiv.org/html/2501.01234
#   year_hint=2025
#   pages=14
#   title=
#   slug=arxiv-2501.01234
#   images_dir=.raw/papers/arxiv-2501.01234/images
#   images_count=<掃除後の embedded 数>
#   image_manifest=.raw/papers/arxiv-2501.01234/images/images.json
#   figure_ids=<一意な図表 ID 数>
#   figure_id_counts=Figure 1:3,Figure 2:1
```

ヘルパーがやること:
1. **入力解決**: `arxiv.org/abs|pdf` URL・裸の arXiv ID(`2501.01234`)→ `https://arxiv.org/pdf/<id>.pdf`。その他 https URL はそのまま。ローカル `.pdf` はコピー。
2. **ダウンロード → 検証**: `.raw/papers/<slug>.pdf` に保存し、先頭 `%PDF` シグネチャを確認。ペイウォール/HTML を掴んだら PDF を消して失敗する(誤った原本を残さない)。
3. **テキスト抽出**: `pdftotext -layout` で `.raw/papers/<slug>.txt` を生成。これは**取り込み時に Read するため**のもので、wiki にそのまま貼らない。
4. **画像抽出**: Mozilla `pdf.js`(`scripts/extract-paper-images.mjs`)で各ページを読み、`.raw/papers/<slug>/images/` に画像を保存する。operator list から取れる埋め込み raster 画像は `image-<page>-<seq>.png` として残す。ヘルパーが中間生成した `page-<page>.png` は、**この実行が作った images ディレクトリだけ**を対象に fetch が抽出直後に掃除する。エージェントは `page-*.png` を消さない。`find` / `python -c` で `images.json` を直さない。
5. **メタ情報の手掛かり**: `arxiv_id` / `arxiv_html` / `year_hint`(arXiv ID から) / `pages` / `pdfinfo` のタイトル / `images_dir` / `images_count`(掃除後) / `image_manifest` / `figure_ids` / `figure_id_counts` を返す。

### サンドボックスとネットワーク

PDF ダウンロードは外部への egress を伴う。サンドボックス下では `arxiv.org` 等が許可ホストに無く**接続拒否で失敗する**ことがある。その場合はヘルパーをサンドボックス無効化で再実行する(ユーザーに許可を求めるプロンプトが出る)。`WebFetch` は処理済みテキストしか返さず PDF バイナリを取れないため、原本取得は `curl`(= ヘルパー)で行う。

### メタデータと abstract の取得

PDF 本文だけでは書誌情報が不足しがちなので、補完する:
- **arXiv**: `arxiv_html`(`https://arxiv.org/html/<id>`)を `WebFetch` で取得すると、タイトル・著者・所属・abstract・本文・図表キャプションが構造化テキストで得やすい(arXiv は PDF/abs に軽いアクセス制限があるため HTML 版を優先)。HTML 版が無い古い論文は abs ページ(`/abs/<id>`)を `WebFetch`。
- **出版社版(IEEE/ACM/USENIX 等)**: `url` は実在するものだけを `WebFetch` で確認して採用。無ければ空。
- **abstract は完全な日本語訳**を一文ずつ忠実に作る(常体)。原文を勝手に要約・補完しない。

### 図表画像の扱い

ヘルパーは全ページから `pdf.js` で画像候補を抽出するが、**`.raw/papers/<slug>/images/` に保持するのは埋め込み画像(`image-*.png`)と `images.json` だけ**とする。`page-*.png` は fetch が掃除まで行う。**手で `find` / `python -c` しない。**

クリーンアップ完了後、**§ Step 0.1** で長編かどうかを判定する。paper のままならすぐに **§ Step 0.5** へ進む。

---

## Step 0.1: 長編サーベイ・thesis ならこのセッションを止める

`pages=` と目次/節構造を見て、`wiki-ingest-thesis` の領分なら**このセッションでは続けない**。判定は thesis スキルと同じ。博士・修士論文は常に thesis。サーベイ・SoK は概ね 30 ページ超かつ章相当のセクション構造の両方。曖昧なら `AskUserQuestion`。

thesis と分かったら:

1. ユーザーに **新しいセッション**で `/wiki-ingest-thesis <同じ URL または PDF>` を起動するよう伝える。
2. このセッションでは `fetch-book.sh` を呼ばない。章 source を書かない。既存の 1 枚 source を消さない。
3. `.raw/papers/` に置いた PDF はそのまま残してよい。thesis セッションが `.raw/theses/` に取り直す。
4. 完了報告は「thesis へ差し戻した」1 行。`python3 scripts/usage-report.py --self --log-line` は出してよい。

同じチャットの続きで thesis を完走すると、常駐文脈が 300k を超え `$10` 帯になる。

---

## Step 0.5: 本文参照図表の抽出と source ページへの埋め込み準備(必須)

**このステップを省略しない。** 初回 ingest で図表埋め込みまで完了させること。後から「図表を埋め込んで」と言われるのは Step 0.5 が抜けたサインである。

> [!important] 本文で参照された図表は必ず取り込み、必ず埋め込む
> 論文本文(Abstract〜Conclusion)が `Figure 3`・`Fig. 3`・`Table 2` のように**名指しで参照している図表は、一枚残らず** attachment に配置し、source ページ本文に埋め込む。「代表図を数枚選ぶ」方式は取らない。枚数上限も設けない。本文が参照している図表は、その論文の主張を支える一次証拠であり、取りこぼしは論拠の欠落と同じだからだ。
> 取り込まないでよいのは、**本文からの参照が一切ない図表**(装飾・著者近影・体裁上のロゴ等)と、下記 §2 の除外リストに当たるものだけである。

### 1. 本文参照図表の一覧を作り、コンタクトシートで対応づける

まず**本文中の参照(inline reference)を洗い出して図表番号のチェックリストを作る**。これが取り込みの必須集合になる。**個別 `Read` の前に**コンタクトシートを 1 回作り、シートを Read する。`image-*.png` / attachment をシート無しで Read してはならない。シートと `index.txt` で番号が付いた attachment / `image-*.png` は Read しない。クロップ結果も、切り出し位置が疑わしいときだけ Read。シート 0 枚(埋め込み画像が無い)なら個別 Read も 0。ベクターは [`references/figures.md`](references/figures.md) のクロップ。本文参照分は全件必須という規律は変えない。

`individual-reads` はコンタクトシート(`sheet-*.png`)を除く画像 Read の実数である。シート 1 枚のあとに attachment を 10 枚読んで 0 と書くことは禁止。

```bash
python3 scripts/wiki-verify-ingest.py --list-figure-ids ".raw/papers/<slug>.txt"
# 1 行 1 ID(`Figure 1	3` = 3 回)。回数 1 はキャプション行だけの候補なので出現位置を目視する
python3 scripts/contact-sheet.py \
  --manifest ".raw/papers/<slug>/images/images.json" \
  --out "$TMPDIR/cs-<slug>" --index-txt
```

出現回数が 2 以上のものは本文参照ありと見なして**必須取り込み**。回数 1 のものはキャプション行だけの可能性があるので、出現位置を確認し、キャプション以外に現れなければ除外してよい(§2)。改行で分断された参照は list では拾えないことがある。キャプション行にあって list に無い番号は、本当に本文参照が無いのか目視してから除外する。list の出力を **図表番号のチェックリスト**として保持し、Step 1 完了時に突き合わせる。arXiv HTML 版があればそちらからも同じ抽出を行う。システム python に Pillow が無いときはコンタクトシートが `uv run --with pillow --cache-dir "$TMPDIR/uv-cache"` で再実行する(サンドボックスで `.git` 作成が拒否されたら権限付きで再実行)。

シートのセルと `index.txt` でファイルへ戻す。斜め見しない。シートが 3 枚を超えるなら本文必須番号に対応する画像だけを `--glob` で渡す。シートを Read しすぎない。チェックリストにあるのに `image-*.png` に対応物が無い図表はベクター描画の可能性が高い。「埋め込み画像が取れなかったから図表は諦める」は認めない。

### 2. 取り込む図表を確定する(本文参照分は全件必須・枚数上限なし)

§1 のチェックリストに載った図表は**すべて取り込む**。優先度による足切りはしない。図表点数の多い論文(10 点超)でも上限を設けず、逐一の重要度判定に時間をかけすぎず機械的にクロップ・埋め込みを進めてよい。

**除外してよいのは次だけ**(これ以外は含める):

- 本文からの参照が一切ない図表(装飾画像・著者近影・ロゴ・体裁上の飾り)
- 凡例のみの画像、ページ全体のスクリーンショット相当の画像
- 本文中のコードブロックで代替可能なコード画像(該当箇所は Markdown コードブロックとして転記する)
- **付録(Appendix)でのみ参照される図表**。本文の主張に直結する場合は含めてよいが、必須ではない
- 同一実験の**ほぼ同一構図の繰り返し**(試行条件だけ異なるバリエーションが 5 点以上続く場合、代表 1〜2 点+残りは本文で言及のみ。何点を省略したかはチャット報告に明記する)

除外した図表番号と理由は、Step 5 完了時のチャット報告に列挙する(source ページ本文には書かない)。

**表(Table)の扱い**: 本文参照のある表は、Markdown 表への忠実な転記をもって「埋め込み済み」とみなす(画像クロップでもよい)。数値・単位・列見出しを落とさず転記し、直後に原キャプション `(Table N. ...)` を添える。行数が多すぎて 1 ページ 300 行の上限を圧迫する場合のみ、画像クロップに切り替える。

### 3. attachment フォルダを作り画像をコピーする

attachment-slug は PDF のスラグ(`<slug>`)と同じ文字列を使う。画像ファイル名は内容が分かる名前に変更する。

```bash
mkdir -p "wiki/sources/_attachments/<slug>"
# 例: 図番号と内容を組み合わせた名前でコピー
cp ".raw/papers/<slug>/images/image-004-001.png" \
   "wiki/sources/_attachments/<slug>/fig02-architecture.png"
cp ".raw/papers/<slug>/images/image-012-001.png" \
   "wiki/sources/_attachments/<slug>/fig05-results-comparison.png"
```

> [!important] 画像ファイル名に `token`(大文字小文字問わず)を含めない
> サンドボックスの Read 許可設定は `**/*token*` を含むパスを一律で読み取り拒否する(トークン・認証情報ファイルの誤読み防止のためのデフォルトルール)。論文が "Token-Level Loss" のような図を持つ場合でも、ファイル名は `fig04-perword-loss.png` のように **token を含まない別名**にする。同様の理由で `secret`・`password`・`credential`・`key` を含む名前も避ける。うっかり `token` を含む名前で保存すると、Read ツールが `File is in a directory that is denied by your permission settings` で失敗する(内容は正しく生成されているので `mv` でリネームすれば直る)。

### 4. source ページへの埋め込み構文

Step 1 で source ページを書くとき、各図はそれが言及されている本文(`## 提案手法`・`## 実験結果` など既存セクション)の近傍——該当する記述の直後——に埋め込む。**「## 図表」のような図表専用セクションは作らない。** 図がまたがる話題が複数セクションにある場合は、最も中心的に論じているセクションを選ぶ。

```markdown
**Figure N: 図の内容(原語タイトルまたは要約)**
![[_attachments/<slug>/fig02-architecture.png]]
(Figure N. キャプション本文の日本語要約。図が示す数値・構成・論点を 1〜2 文で正確に説明。)
```

図が多い論文では、同一セクションに複数の図が連続して並んでもよい。**「多いから減らす」判断はしない。** 各図には必ずキャプション行を付け、本文側でも「Figure 4『Verifier overheads』では…」のように図番号を名指しして、どの記述がどの図に支えられているかを追えるようにする。

表の転記形式は §2 の「表(Table)の扱い」に従う。

---

## Step 0.6: 発表スライドを発見したときの追加取り込み(任意)

スライドが見つかった、またはユーザーが明示したときは、**同じ source ページに統合する**(別 source は作らない)。スライド単体なら `wiki-ingest-slides`。

```bash
bash scripts/fetch-slide-deck.sh "<スライド PDF の URL またはローカルパス>" "<slug>"
```

- ページ画像は全ページ順に読む。論文図と同じ attachment にまとめ、ファイル名は `slide-<内容>.png`。
- **スライド図だけは厳選**(目安 0〜4。論文図の全件主義は適用しない)。**論文図と重複するスライドは避ける。** 論文図は間引かない。`sources:` にスライド PDF を足す。
- **キャプションに由来を書く**(「論文 Figure N」「スライド p.N」)。 `(Source: …)` は使わない。
- スライド用の別 source を作らない。スライドの `page-*.png` は削除しない。音源・動画はユーザー明示時だけ。
- OpenReview の `pdf?id=` が 403 なら突破せず、会議サイトの直リンクから取る。

---

## 再取り込み判定(manifest デルタ追跡)

`.raw/.manifest.json` を見て、未変更ソースの再処理を避ける。キーは PDF パス。

```bash
[ -f .raw/.manifest.json ] && echo exists || echo "no manifest yet"
md5 -q .raw/papers/<slug>.pdf 2>/dev/null || md5sum .raw/papers/<slug>.pdf | cut -d' ' -f1
```

- PDF のハッシュが manifest の記録と一致 → 「取り込み済み(未変更)。`force` で再取り込み」と報告してスキップ。
- 無い/異なる → 取り込みを実行。完了後 `{hash, ingested_at, pages_created, pages_updated}` を記録して書き戻す。
- ユーザーが「force ingest」「再取り込み」と言ったらデルタ判定をスキップ。

manifest 形式(無ければ作成):
```json
{
  "sources": {
    ".raw/papers/arxiv-2501.01234.pdf": {
      "hash": "abc123",
      "ingested_at": "2026-06-02",
      "pages_created": ["wiki/sources/2025__arXiv__Title.md", "wiki/entities/Some Author.md"],
      "pages_updated": ["wiki/concepts/異常検知.md"]
    }
  }
}
```

---

## Step 1: source ページを作る(`wiki/sources/`)

論文を読み切ってから書く。`.raw/papers/<slug>.txt` を**全部**読む(斜め読みしない)。長い論文は分割して読む。abstract と書誌は HTML 版/abs ページで裏取りする。

### ファイル名(`@` プレフィックス必須)

`wiki/sources/@YYYY__SOURCE__Title.md`。**先頭に必ず `@` を付ける**。`SOURCE` は**発表媒体の通称略号**。arXiv プレプリントなら `arXiv`。査読会議/論文誌で出ていればその略号(`NSDI`・`MLSys`・`ACCESS` 等)。セパレータはアンダースコア 2 つ、語間スペースは保持、禁則文字(`/` `:`)は前後を空白で囲んで置換。

例: `@2025__arXiv__Efficient Fault Localization for Microservices.md`

`@` が無いと basename が `papers/` と衝突し、ベアリンクは浅い `papers/` 側へ解決される。**wiki 内リンクは全て `@` 付き。** `papers/` を意図的に指すときだけパス修飾する。`wiki-mode.py route source` ではなくこの命名を使う(conventions §4)。

### frontmatter(conventions §2/§3 準拠)

```yaml
---
address: c-NNNNNN          # wiki-page-write.py が採番して先頭キーに挿入する(--address は既採番の値だけ)
type: source
title: "論文タイトル(原語)"
date: 2026-06-02 18:46    # 取り込み日時 JST(必須)
created: 2026-06-02
updated: 2026-06-02
aliases:
  - "{1st-author-lastname}+, {SOURCE}{year}"   # 例: "Ro+, arXiv2025"
  - "{手法名があれば}"
tags:
  - 2026/06/02            # 日付タグを先頭(必須・最優先)
  - source
  - <domain-tag>          # 例: aiops, distributed
status: developing
related: []
sources:
  - "[[.raw/papers/<slug>.pdf]]"   # 取り込んだ PDF 原本
  - "[[.raw/papers/<slug>.txt]]"   # 抽出テキスト
  - "[[.raw/papers/<slug>/images/images.json]]"   # pdf.js 画像抽出 manifest
source_type: paper
author: "1st Author ほか"
date_published: YYYY-MM-DD          # 不明なら年だけ。arXiv は投稿日
url: ""                             # 出版社版/arXiv abs の実在 URL のみ
confidence: high
key_claims:
  - "論文の中心主張1(本文・図表に遡及可能なもの)"
  - "中心主張2"
---
```

### 本文(create-paper-note 由来の深掘りメモ)

見出しは `##` 以下を使う(ページ先頭の H1 タイトルは Obsidian の title で代替されるため省略可)。すべて**日本語常体**。**出典に無いことは書かない**——訓練データの知識・推測・もっともらしい埋め草は禁止。各主張は PDF 本文・図表に遡及できること。

```markdown
> [!abstract] 概要(arXiv abstract の日本語訳)
> {abstract を一文ずつ忠実に和訳。要約や補完をしない}

## 論文情報
- タイトル / 著者・所属 / 媒体 / 発表年
- arXiv ID・DOI・コード URL(あれば)

## 概要
論文全体の要約を 2-3 文(常体)。

## 問題設定
入力と出力、前提条件、必要なデータ。

## 提案手法
- **アーキテクチャ**: 全体像と各コンポーネントの役割。
- **アルゴリズム/手法の詳細**: 数式・擬似コード・ステップごとの論理。核となる処理を具体的に。
- **実装上の工夫**: 性能向上や課題解決のための具体的技術。

## 新規性
既存手法の課題と、本研究の解決方法を先行研究と比較して具体的に。

## 実験設定
- 実験環境(ハードウェア/ソフトウェア) / データセット(出所・規模・特性) / 比較対象(baseline と選定理由) / 評価指標(定義式が要るものは式も)。

## 実験結果
- 定量評価(図表の具体的数値・改善率を引用) / アブレーション / 定性評価。

## 考察
- 結果の解釈・優位性の根拠・限界と例外。

## 強み / 弱点・課題
- Strengths と Weaknesses/Limitations。論文が述べる課題と、読み取れる懸念。
```

濃度の指針: 1 ページ 100〜300 行を上限(conventions §7)。超えそうなら、深掘りの一部を concept ページに逃がす。**ただし図表の埋め込み行(画像埋め込み+キャプション)は削減対象にしない。** 行数が上限を圧迫するなら、削るのは散文の深掘りであって図表ではない。図表を埋めるときは数値・式・キャプションを正確に。「どの図が根拠か」を本文で名指しする(例: 「Figure 4『Verifier overheads』では…」)。

**Step 0.5 で確定・配置した画像は、それを裏付ける記述があるセクション内(その記述の近傍)に埋め込む。図表専用セクションは作らない。** 埋め込みは Step 1 の執筆と同時並行で行い、source ページの初稿完成時点で**本文参照図表がすべて埋め込まれている状態**にする。本文に `Figure N` と書いたのにその図が埋め込まれていない、という状態を残さない。

---

## Step 2: entity ページ(`wiki/entities/`)

論文に出てくる**人・組織・製品/システム・リポジトリ・データセット**ごとに 1 ページ。既存の有無は index ではなく名前解決で確認する。**登場する全 entity 名を 1 回の呼び出しで解決する**(1 名ずつ呼ばない。呼び出し 1 回が常駐文脈 1 回分の費用)。

```bash
python3 scripts/wiki-resolve.py entity:"<著者A>" entity:"<著者B>" entity:"<所属>" entity:"<システム>" --compact
```

`--compact` は 1 クエリ 1 行(`<クエリ>\t<type>\tHIT|NONE\t<path>(<match> <score>); ...`。パスは `wiki/` からの相対)。`HIT` なら更新、`NONE` なら新規作成。名前が多いときは `--names-file <行区切りのファイル>` でもよい。

**書き込み直前に、作る予定の entity をもう一度 resolve する**(取り込み開始時の結果を使い回さない。並行セッションが先に作っている)。

- HIT、または `wiki-page-write.py` が「既存ファイルがある」で拒否 → `wiki-append.py` だけ。`Write` / `--force` で上書きしない。
- NONE → `wiki-page-write.py` だけ。`Write` ツールで新規 entity を作らない。
- 自分が書いたページから、このソースへの言及が消えている(他セッションの上書き) → 複数ファイルをマージ復旧しない。ユーザーに止めて確認する。

- ファイル名は**原名**(大文字・スペース保持。例 `Yeonju Ro.md`、`Carnegie Mellon University.md`)。`route entity` の出力どおり。これにより `structures/*.MOC.md` 内の `[[...]]` と名前空間が一致する。
- `entity_type`: `person|organization|product|repository|place|dataset`。
- **`entity_tier` で stub / full を使い分ける**(論文に出てくる全員を full ページにしない):
  - `entity_tier: stub` — 初出の共著者・一度きりの言及。frontmatter + 所属/役割 2〜3 行だけ。本文を育てない。
  - `entity_tier: full` — 2 ソース目以降、または繰り返し登場する組織/製品/ハブ(書籍・thesis 含む)。
  - 既存の本文ページは `full` とみなす。`entity_tier` が無いページを遡及改名しない。stub の更新は frontmatter だけでよい(excerpt 不要)。
- 著者ページの `aliases` には姓だけの別名(`Ro` 等)も入れて検索性を上げる。
- `first_mentioned: "[[@2025__arXiv__Title]]"`(この source ページ。`@` 付き)。
- **新規作成する entity ページにも `address:` を採番する**(`wiki-page-write.py` が先頭キーに挿入する。source と同じ位置)。既存 entity への追記は `wiki-append.py` の `section_append`(例: `## 関連ソース`)を使い、既存の `address:` はそのまま保持して再採番しない。

---

## Step 3: concept ページ(`wiki/concepts/`)— 導入の主目的

論文が扱う**重要な手法・枠組み・問題ドメイン**(例: Fault Localization、分散トレーシング、異常検知)を `wiki/concepts/<原名>.md` に育てる。これがこの vault に欠けていた cross-project 概念集約であり、本スキルの主眼。

既存 concept の有無は index ではなく resolve で確認する。**候補語をまとめて 1 回で解決する**(Step 2 の entity 解決と同じ呼び出しに混ぜてよい)。

```bash
python3 scripts/wiki-resolve.py concept:"<候補1>" concept:"<候補2>" concept:"<候補3>" --compact
```

**単一論文で閉じる用語は source ページに留める。** concept を新設するのは、(a) resolve で既存がヒットしない、かつ (b) 2 ソース以上にまたがるか、今後またがることが明らかなハブのときだけ。**1 回の取り込みで concept は新規最大 3、更新最大 5。** 溢れた候補は Step 4 の log エントリに `Deferred:` 行として残し、次の関連ソースで育てる。

既存 concept を更新するときは全文を `Read` しない。`wiki-excerpt.py` で必要節だけ読む。**触る concept を並べて 1 回で読む**(ページ間は `=== <パス> ===` 行で区切られる)。

```bash
python3 scripts/wiki-excerpt.py "wiki/concepts/<原名A>.md" "wiki/concepts/<原名B>.md" --outline   # 主題節の地図(見出しと太字の命題行だけ)
python3 scripts/wiki-excerpt.py "wiki/concepts/<原名A>.md" "wiki/concepts/<原名B>.md" \
  --sections 定義,子概念,未解決の問い,未編纂の観察 --tail 15 --budget-tokens 1800
```

`--total-budget` を使うなら `N * --budget-tokens` 以上にする。更新上限 5 なら 9000 以上。切れたページは読んだことにしない。

**ハブ concept**(`## 子概念` がある、または厚いページ)は excerpt だけ読み、地図として扱う。具体的な知見は最も近い**子 concept** へ書く。ハブへは子へのリンクと、子をまたぐ観察だけを足す。

規約は conventions §8。ingest の追記先は `## 未編纂の観察`(旧名 `## 横断的知見`)と `## 未解決の問い` だけ。**主題節を書き換えない。** 追記は `wiki-append.py` で 1 ページ 1 回。単一ソースで言える事実は受信箱に書かない。補強・反証する観察は冒頭に `[節名]`。矛盾は `> [!contradiction]`。見出しに「知見」「観察」「考察」「まとめ」「横断的」を使わない。

---

## Step 4: 索引・ホット・ログ・manifest の更新

source/entity/concept を書き終えたら、消費者向けメタを更新する。**カタログファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)を Read/Edit しない。** `wiki-catalog.py` で追記する(自己 lock。カタログに `wiki-lock.sh` を二重に取らない)。

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-paper | 論文タイトル
- Source: `.raw/papers/<slug>.pdf`
- Summary: [[@2025__arXiv__Title]]
- Pages created: [[Page 1]], [[Page 2]]
- Pages updated: [[Page 3]]
- Key insight: 新しく分かったことを一文で。
- Deferred: [[候補 concept]] — 今回の上限超過。次の関連ソースで。
EOF
)"

python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
## YYYY-MM-DD | ingest-paper | 論文タイトル
- Focus: [[@2025__arXiv__Title]]。要点。
- Key insight: 一文。
- New: [[Page 1]]
- Updated: [[Page 3]]
EOF
)"

python3 scripts/wiki-catalog.py prepend-changelog \
  --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest-paper | タイトル\n- 新規 concept: [[A]]\n"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/concepts/_index.md \
  --section "現行コンセプトカタログ" \
  --line "- [[A]]"

python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest-paper | タイトル\n- Source: [[@...]]\n"
```

1. 新規 source/entity/concept ごとに、該当 `_index.md` へ `prepend-changelog` + `add-catalog-line`(必要な section を指定)。
2. 大局が変わったら `wiki/overview.md` を更新(overview は通常サイズのため Read/Edit 可)。
3. `.raw/.manifest.json` に `{hash, ingested_at, pages_created, pages_updated}` を記録して書き戻す。

### BM25 索引の差分更新

作成・更新した wiki ページを retrieve 索引に反映する。

```bash
python3 scripts/wiki-retrieve-refresh.py \
  --pages wiki/sources/@YYYY__SOURCE__Title.md wiki/entities/... wiki/concepts/... \
  --no-llm
```

`--pages` には今回作成・更新した wiki ページパスを列挙する。commit 前(Step 5)に必ず実行する。

---

## ページ書き込み手順(ロックはヘルパーが取る)

新規ページは 1 回、既存ページへの追記も 1 回。ロック取得・解放はヘルパーの内側にある。

```bash
# 新規: source・entity・concept をまとめて作る
cat > /tmp/pages.json <<'EOF'
[{"path": "wiki/sources/@2025__arXiv__Title.md", "content_file": "/tmp/source.md"},
 {"path": "wiki/entities/Jane Doe.md",           "content_file": "/tmp/entity.md"}]
EOF
python3 scripts/wiki-page-write.py --batch /tmp/pages.json

# 既存: concept の受信箱・問い・関連・出典・frontmatter sources を一括更新
cat > /tmp/appends.json <<'EOF'
[{"page": "wiki/concepts/異常検知.md", "date": "YYYY-MM-DD",
  "observations": ["[節名] 観察(Source: [[@2025__arXiv__Title]])"],
  "questions_add": ["残った問い"],
  "questions_remove_containing": ["解決済みの問いの一部"],
  "related": {"ソース": ["[[@2025__arXiv__Title]]"], "エンティティ": ["[[Jane Doe]]"]},
  "sources_section": [{"link": "[[@2025__arXiv__Title]]", "note": "何を根拠にしたか"}],
  "frontmatter_sources": ["[[@2025__arXiv__Title]]"]}]
EOF
python3 scripts/wiki-append.py --batch /tmp/appends.json   # --dry-run で差分だけ確認できる
```

いずれもページ単位の JSON を標準出力に 1 行ずつ返し、ロックを取れなかったページだけを終了コード 75 で報告して残りは続行する。追記は冪等なので、同じ spec を再実行すれば取り逃したページだけが適用される。rc=75 は「他の書き手が処理中」なので 2 秒待って 1 回だけ同じヘルパーを再試行し、なお取れなければ上書きせず `wiki-catalog.py prepend-log` に skipped lock として残す。ヘルパーが落ちても新規 source / entity / concept を `Write` で作らない。止めて報告する。`wiki-lock.sh acquire` → `Edit` → `release` は、既存ページの 1 箇所修正でヘルパーが表現できないときだけ(ヘルパー呼び出しをこれで包むと自己デッドロック)。per-file 粒度(別ページは並行可)、60 秒で stale 解除、release は `rm -f`。

---

## 矛盾(contradiction)

新ソースが既存ページと食い違ったら、黙って上書きせず両ページに callout を立てる。

既存ページ側:
```markdown
> [!contradiction] [[新ソース]]と矛盾
> [[既存ページ]]は X と述べる。[[新ソース]]は Y。要解決。日付・文脈・一次資料を確認。
```

新 source ページ側:
```markdown
> [!contradiction] [[既存ページ]]と矛盾
> 本ソースは Y と述べるが既存 wiki は X。詳細は [[既存ページ]]。
```

`[!contradiction]` は `.obsidian/snippets/vault-colors.css` のカスタム callout。スニペットが無くても既定スタイルにフォールバックして動く。

---

## 出典検査(報告はチャットへ、ノート本文に書かない)

`create-conference-note` と同じ規律。書き終えたら主張を一つずつ PDF・図表・abstract に遡及して検査するが、**検査結果はチャット返信で報告し、wiki ページ本文に「検証パス」節を書かない**。

- 全主張を PDF 本文・図表・abstract のいずれかに遡及。どれにも遡れないものは創作 → 削除か、出典が支える内容に置換。
- 数値・固有名は PDF 本文/図表で裏取り。文字起こし的なノイズ(HTML 版の崩れ等)があれば該当文に括弧で注記。
- 推定値(arXiv ID から導いた発行年など)は推定と分かるよう報告。
- 報告は階層化: ✅ 完全に出典裏付けあり / ⚠️ 不確実・要注記 / ℹ️ 推定。明らかな修正は適用し、判断を要する点はユーザーに上げる。

### 図表網羅チェック(必須)

Step 0.5 §1 で作った本文参照図表のチェックリストと、source ページに実際に埋め込まれた図表を突き合わせる。raw との図表番号の集合比較、埋め込みと attachment の両方向照合(埋め込みが実ファイルに解決するか / 使われていない attachment が残っていないか)は 1 コマンドで回る。

```bash
python3 scripts/wiki-verify-ingest.py --pages "wiki/sources/@YYYY__SOURCE__Title.md" \
  --attachments-slug <slug> --figure-ids-from ".raw/papers/<slug>.txt"
```

- `--figure-ids-from` が raw 側の `Figure N` / `Fig. N` / `Table N` を集め、source ページに現れない番号を `FIG-MISSING` として 1 件 1 行で列挙する(§1 の `--list-figure-ids` と同じ抽出)。summary の `figure_ids=` が raw 側の総点数、`figure_ids_missing=` が差分である。
- `unresolved_embeds=0` と `unused_attachments=0` を確かめる(未解決の埋め込みは `EMBED`、未使用の attachment は `ATTACH-UNUSED` として出る)。`embeds=` は埋め込み総数。
- frontmatter の必須項目・H1・wikilink 解決も同じ呼び出しで見る。

差分が出たら、**§2 の除外リストに該当する場合を除き、その場で追加クロップして埋め込む**。「時間が無いので後で」にしない。チャット報告には次を必ず含める。

- 本文参照図表の総数 / 埋め込み済み数
- 除外した図表番号とその理由(除外リストのどれに当たるか)
- 取得を試みたが失敗した図表があれば、その番号と失敗理由(クロップ座標が特定できない等)

---

## Step 5: git commit

ingest の各ステップ(source・entity・concept・index/hot/log/manifest 更新・`wiki-retrieve-refresh.py`)が完了したら、ユーザーの明示指示を待たずに**自動でコミットする**(vault ルート `CLAUDE.md` の「Claude は意味のある作業区切りでは、ユーザーの明示指示を待たずに自動コミットしてよい」という方針に従う。1 論文の ingest = 1 まとまりとして扱う)。今回の ingest で作成・更新した具体ファイルだけを stage する。`git add wiki/ .raw/` のような広い指定は使わない。未関連の dirty file や過去の raw 成果物を巻き込むためだ。

> [!important] 共有メタファイル(`wiki/index.md`・`hot.md`・`log.md`・`wiki/{sources,entities,concepts}/_index.md`・`.raw/.manifest.json`)は他の未コミット ingest の変更を含んでいる場合がある。stage 前に必ず `git status --porcelain=v1 -- <path>` と `git diff -- <path>` で、自分がこのステップで書いた差分だけが含まれているか確認する。他セッションの未コミット変更が混在していたら、コミットメッセージにそのファイルを含めてよいが、コミットメッセージ自体は本 ingest のタイトルのままにし、混在を後から `git log -p` で追跡できるようにする。
>
> 破壊的操作(force push・履歴改変・リモート push)は引き続き明示確認が必要。ローカルコミットの自動化のみがこの規則の対象。

```bash
git add \
  ".raw/papers/<slug>.pdf" \
  ".raw/papers/<slug>.txt" \
  ".raw/papers/<slug>/images/images.json" \
  ".raw/papers/<slug>/images/<selected-embedded-images>" \
  ".raw/slides/<slide-slug>/" \
  ".raw/.manifest.json" \
  "wiki/sources/@YYYY__SOURCE__Title.md" \
  "wiki/sources/_attachments/<attachment-slug>/<selected-images>" \
  "wiki/entities/<updated-entity>.md" \
  "wiki/concepts/<updated-concept>.md" \
  "wiki/index.md" "wiki/hot.md" "wiki/log.md"
git commit -m "wiki: ingest-paper | <論文タイトル>"
```

§ Step 0.6 でスライドも取り込んだ場合は `.raw/slides/<slide-slug>/`(PDF・テキスト・全ページ画像)を丸ごと add する。スライドを取り込まなかった場合はこの行を省く。

不要な候補パスは外してから実行する。コミットメッセージの形式: `wiki: ingest-paper | <論文タイトル>`。タイトルは source ページの `title:` フィールドの値(原語)をそのまま使う。

コミット後に `python3 scripts/usage-report.py --self --log-line` を実行し、出力の 1 行を**チャットの完了報告に含める**(`wiki/log.md` には書かない。log エントリはセッション終了前に書かれるため数値が確定しない)。`--latest` は並行 ingest で他人のセッションを拾うので使わない。環境変数が空で `--self` が落ちたら `--session <このセッションの ID>` を渡し、それでも `--latest` にはしない。振り返りには `--since YYYY-MM-DD` を使う。subagent の使用量は親ログに含まれないので、並列 ingest の値はオーケストレータ側だけのものになる。完了報告には `contact-sheet: N sheets / individual-reads: K` を必ず含める。

---

## やってはいけないこと

- **`.raw/` の既存ファイル(利用者が投入した原本)を変更しない。** ただし本スキルが新規生成する `.raw/papers/<slug>.pdf` と `.txt`、および `.raw/.manifest.json` は対象外(これらは生成・更新してよい)。
- **`papers/`・`research/`・`structures/`・`notes/` を書き換えない。** wiki から一方向リンクで参照するだけ。MOC への逆リンク追記は人間承認時のみ 1 件単位。
- **`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md` の全文を Read しない。** カタログは入力にしない。
- 重複ページを作らない。作成前に必ず `wiki-resolve.py` で既存を確認(index 全文や grep 全走査に頼らない)。
- ログ・ホット・索引の更新は `wiki-catalog.py` で行う(Read/Edit による手書き追記はしない)。すべての取り込みを記録する。
- ペイウォール/HTML を掴んだ壊れた PDF を原本として残さない(ヘルパーが検証して止める)。
- **`image-*.png` / attachment をコンタクトシート無しで Read しない。** 個別 Read はシート上で番号が確定しないセルだけ。シート 0 枚なら個別 Read も 0。
- **`cd` だけの Bash は禁止。** 作業ディレクトリは呼び出しをまたいで持続する。必ず `cd <vault絶対パス> && <実処理>` の 1 呼び出し。
- **`scripts/allocate-address.sh` を直接実行しない。** 採番はヘルパー外では失敗する。`wiki-page-write.py` がロック内で行う。`--address` は既に採番済みの値を渡すときだけ。
- **新規 source / entity / concept を `Write` ツールで作らない。** 新規は `wiki-page-write.py`、既存追記は `wiki-append.py`。
- **並行 ingest で同じ entity を後勝ち上書きしない。** 書き込み直前に resolve し、既存なら append。衝突したらマージ復旧せず止める。
- **シートで番号が付いた attachment を Read しない。** `individual-reads` はシートを除く画像 Read の実数。
- **このセッションのまま `wiki-ingest-thesis` を完走しない。** 長編と分かったら新セッションへ差し戻す。`fetch-book.sh` を paper セッションから呼ばない。
- **完了報告の計測に `--latest` を使わない。** `--self`(失敗時だけ `--session <ID>`)にする。

---

## How to think(10 原則対応)

| # | 原則 | ここでの適用 |
|---|------|------|
| 1 | OBSERVE(ext) | PDF 本文を抽出して全部読む。長い論文でも省略しない。 |
| 2 | OBSERVE(int) | 論文の枠組みに引きずられていないか。異論は contradiction callout に。 |
| 3 | LISTEN | なぜこの論文を wiki に入れるのか。ユーザーが引き出したい横断知識は何か。 |
| 4 | THINK | どの entity・concept がページに値するか。既存ページとの矛盾は。 |
| 5 | CONNECT(lat) | 本論文の主張 vs 既存ソース。矛盾は最高シグナル。 |
| 6 | CONNECT(sys) | `wiki-mode.py route` + `wiki-lock.sh` + `wiki-resolve.py` / `wiki-catalog.py` + `structures/*.MOC.md` への一方向リンク。 |
| 7 | FEEL | 半年後にも効くページか。転写でなく統合を。 |
| 8 | ACCEPT | すべての主張が wiki 向きではない。取捨選択は機能であってバグではない。 |
| 9 | CREATE | source + entity + concept + cross-reference + 必要なら contradiction。 |
| 10 | GROW | 取り込み中に見つけた矛盾は question 化してフォローアップ。 |
