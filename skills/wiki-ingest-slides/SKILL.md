---
name: wiki-ingest-slides
description: "Slide-deck-specialized wiki ingest. Use when the source is a slide PDF or slide sharing page such as SpeakerDeck, with optional audio or video file/URL. Stores the immutable deck under `.raw/slides/SLUG/`, renders every slide page to images, optionally prepares media/transcript, then files the source into the cross-source wiki layer (`wiki/sources/`, `wiki/entities/`, `wiki/concepts/`). Triggers on: 'このスライドを wiki に', 'SpeakerDeck を ingest', 'slide deck を取り込む', 'スライドPDFをwiki化', '音源付きでスライドを取り込む'. Distinct from create-conference-note, which writes a talk note under `research/conferences/`; this skill builds wiki pages and never writes to `research/`. When 3+ decks are given at once, this skill fans out subagents and ingests them in parallel by default."
---

# wiki-ingest-slides: スライド資料特化の wiki 取り込み

スライド PDF を **PDF 原本 + 全ページ画像**として `.raw/slides/<slug>/` に置き、視覚情報を含めて wiki レイヤーに取り込む。任意で音源・動画ファイル・動画 URL を受け取り、文字起こしが利用できる場合は口頭説明・質疑・デモ説明を補助情報として使う。動画本体は vault に保存しない。

このスキルは `wiki-ingest-paper` の兄弟スキルである。違いは、スライドではテキスト抽出だけでは図・矢印・強調・表・グラフ・スクリーンショットを落とすため、**全ページを画像化して必ず読む**点にある。PDF 抽出テキストは検索・補助用途に限る。

> [!important] 最初に必ず読むこと
> wiki を操作する前に [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md)・[`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md)・[`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md) を読む。frontmatter・命名・言語(日本語常体)・出典規約は conventions が優先される。
>
> **カタログ類は Read しない**: `wiki/index.md`・`wiki/{sources,entities,concepts}/_index.md`・`wiki/hot.md`・`wiki/log.md` の全文を `Read` してはならない。既存ページの発見は `wiki-resolve.py`、カタログ更新は `wiki-catalog.py`、本文の部分読みは `wiki-excerpt.py` に任せる。

---

## いつ使うか

| 入力・意図 | 使うスキル | 出力 |
|---|---|---|
| スライド PDF / SpeakerDeck 等を横断知識として wiki 化 | **wiki-ingest-slides(本スキル)** | `.raw/slides/<slug>/` + `wiki/{sources,entities,concepts}/` |
| 会議トークの聴講ノート・Q&A 詳細 | `create-conference-note` | `research/conferences/` |
| 論文 PDF を wiki 化 | `wiki-ingest-paper` | `.raw/papers/*.pdf` + `wiki/` |
| HTML 記事・ブログ等を wiki 化 | `wiki-ingest` | `.raw/articles/` + `wiki/` |

判断基準: **スライド資料を wiki レイヤーに積むなら本スキル**。会議ごとの個別聴講ノートが目的なら `create-conference-note`。このスキルは `papers/`・`research/`・`structures/`・`notes/` を書き換えない。

---

## 複数デッキのバッチ並行 ingest

スライドが3件以上渡されたら、ユーザーが並行処理を指示しなくても**既定で subagent 並行モード**で処理する。プロトコルは汎用 `wiki-ingest` スキルの「Batch Ingest › Parallel mode」節に従う。本スキル固有の要点:

- subagent 1体につきデッキ1件。wiki ページ書き込みは lock 必須
- 共有カタログ(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`・`overview.md`・`.raw/.manifest.json`)と address allocator は subagent からは触らない。オーケストレータが全 subagent 完了後に **`wiki-catalog.py`**(prepend-log / prepend-hot / prepend-changelog / add-catalog-line / prepend-master)で一括更新する(Read/Edit しない)
- PDF 取得・`pdftoppm` の全ページ画像化・文字起こしは CPU/ネットワーク負荷が大きい。同時実行は 3〜4 体までに抑える
- log エントリはデッキごとに1つずつ、commit は最後に1回

---

## 入力

必須:
- スライド PDF URL、ローカル PDF パス、または SpeakerDeck 等の共有ページ URL。

任意:
- 音源ファイル、動画ファイル、音源 URL、動画 URL。
- 公式ページ URL。タイトル・登壇者・概要の裏取りに使う。

出典優先度:
1. **スライド画像**: 数値、図表、式、固有名、引用、コード、画面 UI の権威。
2. **公式ページ**: タイトル、登壇者、所属、概要、発表イベントの権威。
3. **音声/動画 transcript / 字幕**: スライドに載らない口頭説明、背景、デモ説明、Q&A。
4. **PDF 抽出テキスト**: 検索・ページ把握の補助。画像と食い違えば画像を正とする。

> [!important] 公式ページ取得のタイミング
> 公式ページ URL が渡された場合、または**タイトルスライドの登壇者・所属・日付がプレースホルダー(`[ Speaker · Organization · Date ]` 等)や空欄のとき**は、Step 0 の直後に `WebFetch` で公式ページを取得してメタデータを確定させる。後から「所属不明」「日付不明」になるのを防ぐためである。

---

## Step 0: スライド PDF を取得してページ画像を作る

PDF の取得と画像化はヘルパーに任せる。

```bash
bash scripts/fetch-slide-deck.sh "https://speakerdeck.com/..."
bash scripts/fetch-slide-deck.sh "/path/to/deck.pdf" "optional-slug"
bash scripts/fetch-slide-deck.sh --force "/path/to/deck.pdf" "optional-slug"
```

ヘルパーの出力(key=value):

```text
pdf=.raw/slides/<slug>/<slug>.pdf
txt=.raw/slides/<slug>/<slug>.txt
pages_dir=.raw/slides/<slug>/pages
pages=42
title=
slug=<slug>
```

ヘルパーが行うこと:
1. ローカル PDF はコピーし、URL は `curl` で取得する。共有ページ URL の場合はページ内の PDF リンクを発見できるときだけ取得する。発見できない場合は PDF URL を直接指定して再実行する。
2. `%PDF` シグネチャを検証し、HTML やエラーページを原本として残さない。
3. `pdftotext -layout` で補助テキストを作る。
4. `pdftoppm -png -r 180` で全ページを `.raw/slides/<slug>/pages/page-001.png` 形式にレンダリングする。

Poppler(`pdftotext`、`pdfinfo`、`pdftoppm`)は必須依存である。

既存 `.raw/slides/<slug>/` にファイルがある場合、ヘルパーは既定で停止する。`.raw/` 原本の不変性を守るためだ。意図的に再生成する場合だけ `--force` を使う。PDF とページ画像は一時ディレクトリで検証・生成してから最終配置へ移す。

### SlideShare 等ボット対策ページのフォールバック

`slideshare.net` の共有ページは Client Challenge(Cloudflare 等のボット対策)で保護されており、`fetch-slide-deck.sh` の `curl` によるページ取得・PDF発見リンク探索が失敗することがある(`共有ページ内に PDF URL を発見できなかった` エラーで停止)。SlideShare は現行仕様上、匿名ダウンロード可能な PDF を提供しないケースが多い。

判定: 対象 URL が `slideshare.net`(または同様のボット対策を持つ共有サイト)で、`fetch-slide-deck.sh` が PDF URL 未発見エラーを出した場合、以下のフォールバックに切り替える。

1. **`WebFetch` でメタデータと画像 URL パターンを取得する**(`curl` は Client Challenge にブロックされるが `WebFetch` は通ることが多い)。
   ```
   WebFetch(url, prompt="このページのタイトル・登壇者・所属・発表日・イベント名を教えて")
   WebFetch(url, prompt="このページのHTMLソース中に含まれる各スライドの画像URL(image.slidesharecdn.com等のCDN上のjpg/png画像)をすべてリストアップして。imgタグのsrc/data-src属性やJSON内のURLも含めて探して")
   ```
2. 得られた画像 URL から `.../slide-<n>-<width>.jpg` のパターンと総ページ数を確認する。`image.slidesharecdn.com` は `www.slideshare.net` と別ホストで Client Challenge の対象外のため、`curl` で直接ダウンロードできる。
3. 利用可能な最大解像度を width サフィックスを変えて探る(`-2048`・`-1024` は多くの場合 404、`-638` が現実的な最大幅であることが多い、`-320` はより低解像度)。
   ```bash
   for suf in "" "-2048" "-1024" "-638" "-320"; do
     code=$(curl -s -o /dev/null -w "%{http_code}" -A "Mozilla/5.0 (compatible; wiki-ingest-slides/1.0)" \
       "https://image.slidesharecdn.com/<deck-id>/<n>/slide-1${suf}.jpg")
     echo "$suf -> $code"
   done
   ```
4. 判明したパターンで全ページを一括ダウンロードし、PDF を経由せず直接 `.raw/slides/<slug>/pages/page-NNN.jpg` に配置する。`.raw/slides/<slug>/<slug>.pdf` は作らない(PDF 原本は存在しない)。
   ```bash
   mkdir -p ".raw/slides/<slug>/pages"
   for i in $(seq 1 "<pages>"); do
     n=$(printf "%03d" "$i")
     curl -s -o ".raw/slides/<slug>/pages/page-${n}.jpg" \
       -A "Mozilla/5.0 (compatible; wiki-ingest-slides/1.0)" \
       "https://image.slidesharecdn.com/<deck-id>/<n>/slide-${i}-638.jpg"
   done
   ```
5. **PDF 原本なしで進めてよいか、`AskUserQuestion` で一度ユーザーに確認してから続行する**(通常の PDF+poppler フローから外れる判断のため)。取得できた解像度が明らかに読み取り不十分な場合もその旨を伝える。
6. Step 4 の source ページでは、`sources:` に PDF ではなく pages ディレクトリのみを記載し、本文冒頭に `> [!note]` callout で「PDF原本なし、SlideShareのボット対策によりCDN上の個別スライド画像で代替」の旨を明記する。`.raw/.manifest.json` の `note` にも同じ記録を残す(§Step 3)。

このフォールバックは SlideShare 固有の対処だが、他のボット対策付き共有サイトでも同じ考え方(`WebFetch` でメタデータとCDN画像URLパターンを収集 → 別ホストのCDN画像を直接 `curl`)が応用できる可能性がある。

### 任意メディアの準備

音源・動画がある場合だけ、PDF 取得後に実行する。動画入力は音声抽出・文字起こしのためにだけ使い、動画本体は `.raw/slides/<slug>/media/` に保存しない。

```bash
bash scripts/prepare-slide-media.sh "<slug>" "/path/to/talk.mp4"
bash scripts/prepare-slide-media.sh "<slug>" "https://www.youtube.com/watch?v=..."
bash scripts/prepare-slide-media.sh --force "<slug>" "https://example.com/talk.mp3"
```

ヘルパーの出力(key=value):

```text
media_dir=.raw/slides/<slug>/media
media=.raw/slides/<slug>/media/<audio-file-or-empty>
audio=.raw/slides/<slug>/media/audio.m4a
transcript=.raw/slides/<slug>/transcript.md
url=<input-url-if-download-unavailable>
slug=<slug>
```

任意依存:
- `ffmpeg`: 動画から音声を抽出する。
- `yt-dlp`: 動画 URL から一時動画や字幕を取得する。
- `whisper`: 利用可能な場合だけ文字起こしを生成する。
- `whisper-cpp`: 自動実行しない。モデル指定が環境依存のため、使う場合は手動で `.raw/slides/<slug>/transcript.md` を配置する。

依存が無い場合は失敗扱いにしない。音声・文字起こしを生成できる範囲で保存し、文字起こしが無ければ「transcript なし」として進める。

**Whisper 失敗時の YouTube 字幕フォールバック**: `transcript=` が空で YouTube URL が提供されているとき、自動字幕の取得を試みる。

```bash
yt-dlp --write-auto-sub --skip-download --sub-lang ja,en \
  -o ".raw/slides/<slug>/transcript" \
  "https://www.youtube.com/watch?v=..."
```

`.vtt` が取得できたら grep 等で本文行を抽出し `.raw/slides/<slug>/transcript.md` に変換する。自動字幕は機械精度のため信頼度はやや下がるが、口頭説明の概要を得るには有効である。取得できなかった場合は「YouTube 字幕フォールバックも失敗」として不確実点に記録し、「transcript なし」で進める。

補助メディアも既存 `.raw/slides/<slug>/media/` または `transcript.md` がある場合は既定で停止する。意図的に再生成する場合だけ `--force` を使う。直接音源 URL(`.mp3`、`.m4a` 等)は `curl` で保存してよい。直接動画 URL(`.mp4`、`.webm` 等)や動画共有 URL は一時ディレクトリにだけ取得し、音声や文字起こしを生成した後に破棄する。

---

## Step 1: 全ページ画像を読む

`.raw/slides/<slug>/pages/page-*.png` を**全ページ順番に確認する**。サンプリングしない。次を必ず拾う:

- 図の構造、矢印、階層、依存関係、フロー。
- 表、グラフの軸、凡例、単位、数値、比較対象。
- 色分け、太字、囲み、注釈、スライド上の強調。
- スクリーンショット内の UI、ログ、コード断片、エラーメッセージ。
- 章立て、ページ番号、タイトルスライド、まとめスライド。

PDF 抽出テキストは、用語検索・見出し確認・ページ対応の補助として読む。抽出テキストにだけ出る文言は、画像で裏取りできない限り弱い根拠として扱う。

画像の文字が小さく読めない、グラフ値が正確に読めない、動画でしか確認できないデモがある場合は推測しない。source ページまたはチャット報告で不確実点として扱う。

スライド上の `*` 付き参照や `*DLC`・`*TBD` のような文脈なしでは解釈できない略語・注釈は推測しない。source ページの限界・不確実点に「`*XX` の意味不明(p.N)」として記録し、transcript で言及があれば補足する。解決できなければ対応 concept ページの `## 未解決の問い` にも残す。

---

## Step 2: 音声/動画 transcript を読む(任意)

`transcript.md` がある場合は読む。transcript はスライドに載らない口頭説明を補うために使う:

- 背景、動機、設計判断、デモの説明、質疑応答。
- スライド上の図表を登壇者がどう解釈したか。
- スライドに無い制約、失敗例、未解決課題。

数値・固有名・式・図表・引用はスライド画像を優先する。transcript 由来の内容は「口頭説明では…」「質疑では…」のように出典層が分かる表現にする。動画にスライド外のデモ画面やホワイトボードが映る場合は、可能なら代表フレームを抽出して視覚的根拠として扱う。

---

## Step 3: 再取り込み判定

`fetch-slide-deck.sh` はスライドを取得・配置するが、**slug ディレクトリが既存の場合は既定で停止**する。この物理ゲートとは別に、manifest による**セマンティックゲート**を設ける。ヘルパー実行後に `.raw/.manifest.json` を確認し、同ハッシュの既取り込みエントリがあれば wiki ページの新規作成・更新をスキップする。キーは PDF パス。

```bash
md5 -q .raw/slides/<slug>/<slug>.pdf 2>/dev/null || md5sum .raw/slides/<slug>/<slug>.pdf | cut -d' ' -f1
```

- 一致: 「取り込み済み(未変更)。`--force` で再取り込み」と報告してスキップ。
- 無い/異なる/force: 取り込みを実行。
- 完了後 `{hash, ingested_at, pages_created, pages_updated, note}` を `.raw/.manifest.json` に記録する。`note` には「transcript なし(Whisper 失敗)」「YouTube 字幕で補完」「プレースホルダーメタデータを公式ページから補完」「PDF原本なし、SlideShareのボット対策によりCDN上の個別スライド画像で代替」等の補足を任意で入れる。PDF が存在しないフォールバック取り込みの場合、`hash` には PDF の md5 の代わりに固定文字列(例: `no-pdf-cdn-jpg-fallback`)を入れてよい。

---

## Step 4: source ページを作る

ファイル名は conventions に従い、`wiki/sources/@YYYY__SOURCE__Title.md` とする。`SOURCE` は SpeakerDeck、イベント名、企業名、会議名など、資料の公開元として読者が理解できる略号を使う。

frontmatter 例:

```yaml
---
type: source
title: "Slide Deck Title"
date: 2026-06-16 10:00
created: 2026-06-16
updated: 2026-06-16
aliases:
  - "SpeakerDeck 2026"
tags:
  - 2026/06/16
  - source
  - slides
status: developing
related: []
sources:
  - "[[.raw/slides/<slug>/<slug>.pdf]]"
  - "[[.raw/slides/<slug>/pages]]"
source_type: slides
author: ""
date_published: YYYY-MM-DD
url: ""
confidence: high
key_claims:
  - "スライド画像に遡及できる中心主張"
---
```

`sources:` の `[[.raw/slides/<slug>/transcript.md]]` と `[[.raw/slides/<slug>/media/...]]` は、実ファイルが存在する場合だけ追加する。SlideShare フォールバック等で PDF 原本が存在しない場合は `[[.raw/slides/<slug>/<slug>.pdf]]` の行を省き、`[[.raw/slides/<slug>/pages]]` のみを残す。

`date_published` が正確に判明しない場合: 会議の開催期間が分かるなら `YYYY-MM` または `YYYY` を使う。推定でしかない場合は `confidence: medium` に下げ、source ページの限界・不確実点に「発表日は推定」と記録する。

本文の基本形:

```markdown
## 概要
資料全体の要約を 2-3 文で書く。

## 主要メッセージ
- スライド資料が伝える中心主張。ページ番号や図表名を添える。

## 視覚的に重要な図表

**p.12 アーキテクチャ図**
![[_attachments/<slug>/page-012.png]]
A から B への制御フローと C へのデータフローを分けて示す。

**p.18 比較表**
![[_attachments/<slug>/page-018.png]]
X/Y/Z を latency と cost の 2 軸で比較する。

## 口頭説明・補足
transcript がある場合だけ作る。スライドに無い背景・補足・デモ説明を書く。

## Q&A
transcript に質疑がある場合だけ作る。

## 概念・実体への接続
- 関連 entity / concept への wikilink。

## 限界・不確実点
- 読めない値、未取得メディア、transcript なし等。
```

視覚的に重要な図表には、スライド画像を Obsidian wikilink 形式で埋め込み、直下にその図表が示す内容を 1 文で添える。全スライドを列挙する「スライド別メモ」は書かない。source ページ全体は概ね 80-200 行を目安にし、概念の深掘りは concept ページへ逃がす。

画像の配置と参照:
1. source ページに埋め込む画像は `.raw/` を直接参照せず、`wiki/sources/_attachments/<slug>/` にコピーして管理する。
2. コピーは Step 4 の source ページ作成前にまとめて行う。埋め込む予定のページだけをコピーすれば良い。
   ```bash
   mkdir -p wiki/sources/_attachments/<slug>
   cp .raw/slides/<slug>/pages/page-009.png wiki/sources/_attachments/<slug>/
   ```
3. source ページ内からは source ファイルからの相対 wikilink で参照する。
   ```markdown
   ![[_attachments/<slug>/page-009.png]]
   ```

---

## Step 5: entity / concept ページを更新する

`wiki-ingest-paper` と同じ規律で、登壇者、組織、製品、システム、リポジトリ、データセットを `wiki/entities/` に作成・更新する。既存の有無は `wiki-resolve.py --type entity` で確認する。**共著者・一度きりの言及は `entity_tier: stub`**(frontmatter + 所属/役割 2〜3 行)。

concept ページは、スライドが扱う重要概念を `wiki/concepts/<原名>.md` に育てる。既存 concept は `wiki-resolve.py --type concept` で確認し、更新時は `wiki-excerpt.py --tail 15 --budget-tokens 1800` で必要節だけ読む。**1 回の取り込みで concept は新規最大 3、更新最大 5。** 溢れは Step 6 の log に `Deferred:` として残す。上限内で触れた concept だけ `## 未編纂の観察`(旧名 `## 横断的知見` が残るページではそこ。同じ受信箱)と `## 未解決の問い` を更新する。主題節と `## 定義` は ingest から書き換えない(conventions §8)。追記の前に `wiki-excerpt.py --outline` で既存の命題を見て、補強・反証する観察なら冒頭に `[節名]` を付ける。単一スライドだけで言える事実は source ページに置き、複数ソースの突き合わせで見えた観察だけを受信箱に入れる。

矛盾があれば黙って上書きせず `> [!contradiction]` callout を新旧両方に立てる。

---

## Step 6: 索引・hot・log・manifest を更新する

source/entity/concept を書き終えたら、**カタログファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)を Read/Edit しない。** `wiki-catalog.py` で追記する(自己 lock。カタログに `wiki-lock.sh` を二重に取らない):

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-slides | スライドタイトル
- Source: `.raw/slides/<slug>/<slug>.pdf`
- Visual pages: `.raw/slides/<slug>/pages/`
- Media: `.raw/slides/<slug>/transcript.md` または none
- Summary: [[@2026__SpeakerDeck__Title]]
- Pages created: [[Page 1]], [[Page 2]]
- Pages updated: [[Page 3]]
- Key insight: 新しく分かったことを一文で。
- Deferred: [[候補 concept]] — 今回の上限超過。
EOF
)"

python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
## YYYY-MM-DD | ingest-slides | スライドタイトル
- Focus: [[@2026__SpeakerDeck__Title]]。要点。
- Key insight: 一文。
- New: [[Page 1]]
- Updated: [[Page 3]]
EOF
)"

python3 scripts/wiki-catalog.py prepend-changelog \
  --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest-slides | タイトル\n- 新規 concept: [[A]]\n"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/concepts/_index.md \
  --section "現行コンセプトカタログ" \
  --line "- [[A]]"

python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest-slides | タイトル\n- Source: [[@...]]\n"
```

1. 新規 source/entity/concept ごとに、該当 `_index.md` へ `prepend-changelog` + `add-catalog-line`。
2. 大局が変わったら `wiki/overview.md`(Read/Edit 可)。
3. `.raw/.manifest.json` に `{hash, ingested_at, pages_created, pages_updated, note}` を記録する。
4. **BM25 索引の差分更新**(commit 前に必須):
   ```bash
   python3 scripts/wiki-retrieve-refresh.py --pages wiki/sources/@... wiki/entities/... wiki/concepts/... --no-llm
   ```

---

## Step 7: git commit

wiki ページと `.raw/` 原本をひとつのコミットに含める。

```bash
git add wiki/ .raw/
git commit -m "wiki: ingest-slides | <スライドタイトル>"
```

コミットメッセージの形式: `wiki: ingest-slides | <スライドタイトル>`。タイトルは source ページの `title:` フィールドの値をそのまま使う。

---

## ロック手順

wiki ページを書き込む前後で per-file lock を使う。

```bash
if bash scripts/wiki-lock.sh acquire wiki/concepts/Example.md; then
  # Write/Edit
  bash scripts/wiki-lock.sh release wiki/concepts/Example.md
else
  sleep 2
  bash scripts/wiki-lock.sh acquire wiki/concepts/Example.md && {
    # write …
    bash scripts/wiki-lock.sh release wiki/concepts/Example.md
  } || python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-slides | skipped lock
- Key insight: skipped wiki/concepts/Example.md (locked).
EOF
)"
fi
```

---

## 出典検査(報告はチャットへ、ノート本文に書かない)

書き終えたら、すべての主張をスライド画像・公式ページ・transcript・PDF 抽出テキストのいずれかに遡及する。検査結果はチャット返信で報告し、source ページ本文に「検証パス」節を書かない。

- 数値・固有名・図表・式はスライド画像で裏取りする。
- transcript-only の主張は口頭説明由来だと分かるようにする。
- 画像で読めない値、未取得メディア、文字起こし不鮮明箇所は不確実点として報告する。
- 報告は ✅ 完全に出典裏付けあり / ⚠️ 不確実・要注記 / ℹ️ 推定 に分ける。

---

## やってはいけないこと

- `.raw/` の既存ファイルを変更しない。ただし本スキルが新規生成する `.raw/slides/<slug>/` と `.raw/.manifest.json` は対象外。
- `papers/`・`research/`・`structures/`・`notes/` を書き換えない。
- テキスト抽出結果だけでスライドを読んだことにしない。
- 画像で読めない数値やグラフ値を推測しない。
- 音声/動画 transcript でスライド上の正確な数値・固有名を上書きしない。
- ログ・hot・manifest の更新を省略しない。カタログは `wiki-catalog.py` で更新する(Read/Edit しない)。
