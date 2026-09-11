---
name: wiki-ingest-video
description: "Use when the source is a standalone video URL or local video file, including YouTube, conference recordings, demos, lectures, and talks without a slide PDF as the primary source. Triggers on: 'この動画を wiki に', 'YouTube を ingest', '動画だけを取り込む', '録画を wiki 化', 'video source を ingest'."
---

# wiki-ingest-video: 動画 source 特化の wiki 取り込み

動画 URL またはローカル動画ファイルから **音声 + 文字起こし + 代表フレーム + メタデータ**を `.raw/videos/<slug>/` に置き、wiki レイヤーへ取り込む。動画本体は vault に保存しない。スライド PDF が主入力に無い場合はこちらを使う。

このスキルは動画を source として扱うためのものだ。映像に映るデモ、画面遷移、ホワイトボード、図、字幕、登壇者の口頭説明を組み合わせて読む。単なる transcript ingest ではない。

> [!important] 最初に必ず読むこと
> wiki を操作する前に [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md)・[`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md)・[`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md) を読む。frontmatter・命名・言語(日本語常体)・出典規約は conventions が優先される。
>
> **カタログ類は Read しない**: `wiki/index.md`・`wiki/{sources,entities,concepts}/_index.md`・`wiki/hot.md`・`wiki/log.md` の全文を `Read` してはならない。既存ページの発見は `wiki-resolve.py`、カタログ更新は `wiki-catalog.py`、本文の部分読みは `wiki-excerpt.py` に任せる。

---

## いつ使うか

| 入力・意図 | 使うスキル | 出力 |
|---|---|---|
| YouTube / 録画 / デモ動画 / 講演動画を wiki 化 | **wiki-ingest-video(本スキル)** | `.raw/videos/<slug>/` + `wiki/{sources,entities,concepts}/` |
| スライド PDF が主入力で、音源・動画は補助 | `wiki-ingest-slides` | `.raw/slides/<slug>/` + `wiki/` |
| 会議トークの聴講ノート・Q&A 詳細 | `create-conference-note` | `research/conferences/` |
| 論文 PDF を wiki 化 | `wiki-ingest-paper` | `.raw/papers/*.pdf` + `wiki/` |

判断基準: **動画が主sourceなら本スキル**。スライド PDF を主sourceとして視覚情報を読む場合は `wiki-ingest-slides`。このスキルは `papers/`・`research/`・`structures/`・`notes/` を書き換えない。

---

## 入力

必須:
- YouTube 等の動画 URL、またはローカル動画ファイル。

任意:
- 公式ページ URL。タイトル・登壇者・概要・イベント情報の裏取りに使う。
- 既存 transcript ファイル。動画から自動生成できない場合の代替として使う。

出典優先度:
1. **動画の映像フレーム**: デモ画面、UI、コード、グラフ、ホワイトボード、字幕、画面上の数値・固有名。
2. **公式ページ**: タイトル、登壇者、所属、概要、イベント情報。
3. **動画 transcript / 音声**: 口頭説明、背景、設計判断、質疑応答。
4. **自動抽出メタデータ**: URL、ファイル名、長さ、推定タイトル。推定として扱う。

映像と transcript が食い違う場合、画面に表示された数値・固有名・コードは映像を正とする。音声だけで述べられた解釈や背景は transcript を根拠にしてよい。

---

## Step 0: 動画素材を準備する

音声抽出・代表フレーム抽出・文字起こしはヘルパーに任せる。動画本体はローカル入力を直接参照するか、URL 入力では一時ディレクトリにだけ取得し、派生物を作った後に破棄する。

```bash
bash scripts/prepare-video-source.sh "https://www.youtube.com/watch?v=..."
bash scripts/prepare-video-source.sh "/path/to/talk.mp4" "optional-slug"
bash scripts/prepare-video-source.sh --transcript "/path/to/transcript.md" "/path/to/talk.mp4" "optional-slug"
bash scripts/prepare-video-source.sh --force "https://www.youtube.com/watch?v=..." "optional-slug"
```

ヘルパーの出力(key=value):

```text
video=
audio=.raw/videos/<slug>/audio.m4a
transcript=.raw/videos/<slug>/transcript.md
frames_dir=.raw/videos/<slug>/frames
frames=12
duration=1234.56
title=
url=https://...
metadata=.raw/videos/<slug>/metadata.json
slug=<slug>
```

ヘルパーが行うこと:
1. ローカル動画はコピーしない。URL は `yt-dlp` がある場合だけ一時ディレクトリへ取得し、派生物を作った後に破棄する。取得できない場合は URL を `source.url` と `metadata.json` に記録して続行する。**既定の抽出(web player_client)が `403 Forbidden` で失敗した場合、ヘルパーは自動的に `--extractor-args "youtube:player_client=android"` + 480p 以下のフォーマット指定で再試行する**(YouTube 側が形式によって web player_client からのダウンロードを弾くことがあるため)。この再試行で成功した場合は `download_status` に `processed-transient (player_client=android フォールバック)` と記録される。
2. `ffmpeg` がある場合は音声を `.raw/videos/<slug>/audio.m4a` に抽出する。
3. `ffmpeg` がある場合は代表フレームを `.raw/videos/<slug>/frames/frame-001.jpg` 形式で抽出する。
4. URL 入力では `yt-dlp` の字幕・自動字幕取得を優先し、字幕が得られない場合に `whisper` による文字起こしへ進む。`--transcript <file>` が指定された場合はそのファイルを `.raw/videos/<slug>/transcript.md` に保存する。
5. `metadata.json` に `slug`、`url`、`title`、`video`(常に空文字)、`audio`、`transcript`、`frames_dir`、`frames`、`duration`、`download_status` を保存する。

任意依存:
- `yt-dlp`: 動画 URL から字幕や一時動画を取得する。
- `ffmpeg` / `ffprobe`: 音声抽出、長さ取得、代表フレーム抽出。
- `whisper` または `whisper-cpp`: 文字起こし。`whisper-cpp` はモデル指定が環境依存なので自動実行しない。

依存が無い場合は失敗扱いにしない。保存・抽出できた範囲で進め、不足は source ページの `## 限界・不確実点` とチャット報告に残す。

既存 `.raw/videos/<slug>/` にファイルがある場合、ヘルパーは既定で停止する。`.raw/` 原本の不変性を守るためだ。意図的に再生成する場合だけ `--force` を使う。`--force` は本ヘルパーが生成する `audio.m4a`、`transcript.md`、`frames/frame-*.jpg`、`source.url`、`metadata.json` を置き換え、旧形式の `media/video.*` があれば削除する。

### helper が URL-only で終わった場合

ヘルパー(`prepare-video-source.sh`)は既定の抽出が失敗すると `player_client=android` への切り替えを自動的に再試行する(前節参照)。**それでも URL と `metadata.json` だけを残して終了した場合**は、すぐに諦めず、利用可能なローカルツールで同じ `.raw/videos/<slug>/` を補完する。実行したコマンドと取得可否は最終報告に残す。

推奨順:
1. `yt-dlp --list-subs <url>` で字幕を確認し、`--write-subs` または `--write-auto-subs` で VTT を保存する。
2. VTT が取れたら、時刻つきの `transcript.md` に変換する。自動字幕は source ページの `## 限界・不確実点` で明記する。
3. `yt-dlp` で低解像度の動画取得を試す。ヘルパー既定の `player_client=android` 再試行がすでに失敗している場合、次に試す価値があるのは `--extractor-args "youtube:player_client=web_embedded"` や `player_client=ios` など他の client への切り替えである(YouTube 側の配信制限は client ごとに変わることがある)。取得できた場合も `.raw/videos/<slug>/media/video.*` には移さず、派生物を作ったら破棄する。
4. `ffmpeg` で `audio.m4a` と `frames/frame-*.jpg` を生成する。代表フレームは全て目視し、読めない小さい文字や数値は推測しない。
5. `metadata.json` を実ファイルに合わせて更新する。最低限 `video`(空文字)、`audio`、`transcript`、`frames_dir`、`frames`、`duration`、`download_status` を現状に合わせる。`download_status` には試した client と結果を短く記録する(例: `manual-fallback: player_client=android 再試行 -> scratchpad tmpdir へ手動DL -> ffmpeg 抽出後に動画本体は破棄`)。

この手動補完でも `.raw/` の不変性を守る。既存の別ソース素材を上書きしない。既に同じ slug の素材がある場合は、`--force` 相当の意図が明確なときだけ置き換える。

> [!note] 一時ディレクトリはサンドボックス書き込み許可のあるパスを使う
> 手動で `mktemp -d` を呼ぶ場合、既定の `/tmp` や `/var/folders/...` はサンドボックス環境で書き込み拒否されることがある。`"${TMPDIR:-/tmp}"` 配下(スクラッチパッドディレクトリ)を明示的に使う。ヘルパー自身は既にこの形で `TMPDIR` を尊重している。

---

## Step 1: 代表フレームと transcript を読む

`.raw/videos/<slug>/frames/frame-*.jpg` がある場合は全て確認する。次を拾う:

- デモ画面、UI、ターミナル、ログ、コード、設定値。
- グラフ、表、図、ホワイトボード、字幕、画面上の固有名。
- 場面転換、章タイトル、まとめ画面、Q&A の画面表示。

`transcript.md` がある場合は全体を読む。transcript は口頭説明の根拠である:

- 動機、背景、設計判断、失敗例、制約、未解決課題。
- 映像に映る内容を登壇者がどう解釈したか。
- Q&A。質問者名・所属は明示されている場合だけ記録する。

代表フレームが無い場合でも transcript だけで進められるが、映像に依存するデモ・UI・図表は「未確認」として扱う。transcript が無い場合は、映像フレームと公式ページだけで書ける範囲に絞る。

---

## Step 1.5: source 添付画像を作る

source ページ本文から `.raw/` を直接リンクしない。`## 映像で確認できる重要点` で参照する必要なフレームだけを `wiki/sources/_attachments/<attachment-slug>/` にコピーし、本文ではそのコピーを `![[_attachments/<attachment-slug>/...]]` の Obsidian 画像埋め込みで表示する。

```bash
mkdir -p "wiki/sources/_attachments/<attachment-slug>"
cp ".raw/videos/<slug>/frames/frame-003.jpg" "wiki/sources/_attachments/<attachment-slug>/frame-003.jpg"
cp ".raw/videos/<slug>/frames/frame-007.jpg" "wiki/sources/_attachments/<attachment-slug>/frame-007.jpg"
```

`<attachment-slug>` は短く安定した ASCII slug にする。例: `srecon26-brush-taming-unpredictable`。source ページ名そのものはスペースや記号を含みやすく、Obsidian の埋め込み表示が崩れることがあるため使わない。コピーした添付画像は source ページと一緒に git 管理する。`.raw/videos/<slug>/frames/` は不変原本として残すが、閲覧用リンクには使わない。

---

## Step 2: 再取り込み判定

`.raw/.manifest.json` を見て、キー `.raw/videos/<slug>` の記録と比較する。URL 入力は `url` を使う。ローカル動画入力は、vault 外の元ファイルから取得前に計算した `source_hash` を使う。

```bash
md5 -q /path/to/talk.mp4 2>/dev/null || md5sum /path/to/talk.mp4 | cut -d' ' -f1
```

- `source_hash` または URL が manifest と一致: 「取り込み済み(未変更)。`force` で再取り込み」と報告してスキップ。
- 無い/異なる/force: 取り込みを実行。
- 完了後 `{source_hash, url, ingested_at, pages_created, pages_updated}` を `.raw/.manifest.json` に記録する。

manifest 形式(無ければ作成):

```json
{
  "sources": {
    ".raw/videos/youtube-aC_VLx7R2uI": {
      "source_hash": "abc123",
      "url": "https://www.youtube.com/watch?v=aC_VLx7R2uI",
      "transcript_hash": "def456",
      "frames_count": 12,
      "ingested_at": "2026-06-16",
      "pages_created": ["wiki/sources/@2026__YouTube__Title.md"],
      "pages_updated": ["wiki/concepts/Example.md"]
    }
  }
}
```

URL-only の場合は `source_hash` と `transcript_hash` を空文字にし、`url` と `frames_count: 0` を記録する。

---

## Step 3: source ページを作る

ファイル名は conventions に従い、`wiki/sources/@YYYY__SOURCE__Title.md` とする。`SOURCE` は YouTube、イベント名、企業名、会議名など、動画の公開元として読者が理解できる略号を使う。

frontmatter 例:

```yaml
---
type: source
title: "Video Title"
date: 2026-06-16 10:00
created: 2026-06-16
updated: 2026-06-16
aliases:
  - "Video Title"
tags:
  - 2026/06/16
  - source
  - video
status: developing
related: []
sources:
  - "[[.raw/videos/<slug>/audio.m4a]]"
  - "[[.raw/videos/<slug>/transcript.md]]"
  - "[[.raw/videos/<slug>/frames]]"
  - "[[.raw/videos/<slug>/metadata.json]]"
source_type: video
author: ""
date_published: YYYY-MM-DD
url: ""
confidence: medium
key_claims:
  - "動画または transcript に遡及できる中心主張"
---
```

実ファイルが存在するものだけを `sources:` に残す。動画ファイルへの wikilink は書かない。URL 入力の場合は `.raw/videos/<slug>/source.url` と `metadata.json` を残す。

本文の基本形:

```markdown
## 概要
動画全体の要約を 2-3 文で書く。

## 主要メッセージ
- 動画が伝える中心主張。映像・transcript の根拠層を添える。

## 映像で確認できる重要点
- ![[_attachments/<attachment-slug>/frame-003.jpg]]
  frame-003 のデモ画面では、...
- ![[_attachments/<attachment-slug>/frame-007.jpg]]
  frame-007 のグラフでは、...

## 口頭説明・補足
transcript がある場合だけ作る。背景・設計判断・補足説明を書く。

## Q&A
transcript に質疑がある場合だけ作る。

## 概念・実体への接続
- 関連 entity / concept への wikilink。

## 限界・不確実点
- 未取得動画、代表フレームなし、transcript なし、音声不鮮明等。
```

`## 時系列メモ` は作らない。長い動画でも全体を時刻順に再要約せず、重要な画面は `## 映像で確認できる重要点` に代表フレームへの Obsidian 画像埋め込み付きで置き、口頭説明は `## 口頭説明・補足` に論点単位でまとめる。フレーム画像は `.raw/` ではなく `wiki/sources/_attachments/<attachment-slug>/` へコピーした画像を指す。`wiki/sources/` 配下の source ページからは `![[_attachments/<attachment-slug>/frame-001.jpg]]` の形を使う。

---

## Step 4: entity / concept ページを更新する

登壇者、組織、製品、システム、リポジトリ、データセットを `wiki/entities/` に作成・更新する。既存の有無は `wiki-resolve.py --type entity` で確認する。**共著者・一度きりの言及は `entity_tier: stub`**(frontmatter + 所属/役割 2〜3 行)。

concept ページは、動画が扱う重要概念を `wiki/concepts/<原名>.md` に育てる。既存 concept は `wiki-resolve.py --type concept` で確認し、更新時は `wiki-excerpt.py --tail 15 --budget-tokens 1800` で必要節だけ読む。**1 回の取り込みで concept は新規最大 3、更新最大 5。** 溢れは Step 5 の log に `Deferred:` として残す。台帳にも積む: `python3 scripts/concept-candidates.py add --name <候補> --source "[[@<今回の source>]]" --reason "上限超過"`。`wiki-resolve.py` が `ledger:<名>(<k> docs, pending)` を返した候補は既に保留中なので同じコマンドで言及を足し、`ready`(2 文書以上)なら今回の新規枠で優先して新設し `promote` する(conventions §12 ルール 5)。新設か保留かで迷う候補は `python3 scripts/wiki-profile.py`(研究関心の要約 25 行、profile 全文は読まない。終了 3 なら関心判定を飛ばす)に照らす。対象外の用語は concept にせず source に留め、コア関心の候補は 2 文書目で優先して新設する(conventions §12 ルール 3)。上限内で触れた concept だけ `## 未編纂の観察`(旧名 `## 横断的知見` が残るページではそこ。同じ受信箱)と `## 未解決の問い` を更新する。主題節と `## 定義` は ingest から書き換えない(conventions §8)。追記の前に `wiki-excerpt.py --outline` で既存の命題を見て、補強・反証する観察なら冒頭に `[節名]` を付ける。単一動画だけで言える事実は source ページに置き、複数ソースの突き合わせで見えた観察だけを受信箱に入れる。

矛盾があれば黙って上書きせず `> [!contradiction]` callout を新旧両方に立てる。

---

## Step 5: 索引・hot・log・manifest を更新する

source/entity/concept を書き終えたら、**カタログファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)を Read/Edit しない。** `wiki-catalog.py` で追記する(自己 lock。カタログに `wiki-lock.sh` を二重に取らない):

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-video | 動画タイトル
- Source: URL またはローカル入力から計算した source hash
- Transcript: `.raw/videos/<slug>/transcript.md` または none
- Frames: `.raw/videos/<slug>/frames/`
- Summary: [[@2026__YouTube__Title]]
- Pages created: [[Page 1]], [[Page 2]]
- Pages updated: [[Page 3]]
- Key insight: 新しく分かったことを一文で。
- Deferred: [[候補 concept]] — 今回の上限超過。
EOF
)"

python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
## YYYY-MM-DD | ingest-video | 動画タイトル
- Focus: [[@2026__YouTube__Title]]。要点。
- Key insight: 一文。
- New: [[Page 1]]
- Updated: [[Page 3]]
EOF
)"

python3 scripts/wiki-catalog.py prepend-changelog \
  --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest-video | タイトル\n- 新規 concept: [[A]]\n"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/concepts/_index.md \
  --section "現行コンセプトカタログ" \
  --line "- [[A]]"

python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest-video | タイトル\n- Source: [[@...]]\n"
```

1. 新規 source/entity/concept ごとに、該当 `_index.md` へ `prepend-changelog` + `add-catalog-line`。
2. 大局が変わったら `wiki/overview.md`(Read/Edit 可)。
3. `.raw/.manifest.json` に `{source_hash, url, ingested_at, pages_created, pages_updated}` を記録する。
4. **BM25 索引の差分更新**(commit 前に必須):
   ```bash
   python3 scripts/wiki-retrieve-refresh.py --pages wiki/sources/@... wiki/entities/... wiki/concepts/... --no-llm
   ```

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
## [YYYY-MM-DD] ingest-video | skipped lock
- Key insight: skipped wiki/concepts/Example.md (locked).
EOF
)"
fi
```

シェル環境によって `bash`、`dirname`、`sha1sum` などの探索に失敗することがある。ロック操作が PATH 起因で失敗したら、迂回せず vault root と標準 PATH を明示して再実行する。

```bash
/usr/bin/env PATH=/bin:/usr/bin:/usr/sbin:/sbin \
  WIKI_LOCK_VAULT="$PWD" \
  /bin/bash scripts/wiki-lock.sh acquire wiki/concepts/Example.md
```

複数ファイルをロックした場合は、最後に同じ形式で全て `release` し、`list` が空であることを確認する。途中失敗した場合も、取得済みロックを解放してから報告する。

---

## 出典検査(報告はチャットへ、ノート本文に書かない)

書き終えたら、すべての主張を映像フレーム・公式ページ・transcript・動画メタデータのいずれかに遡及する。検査結果はチャット返信で報告し、source ページ本文に「検証パス」節を書かない。

- 画面に表示された数値・固有名・コードは映像フレームで裏取りする。
- transcript-only の主張は口頭説明由来だと分かるようにする。
- 自動生成 transcript の固有名・数値は、映像か公式ページで裏取りできなければ不確実にする。
- 報告は ✅ 完全に出典裏付けあり / ⚠️ 不確実・要注記 / ℹ️ 推定 に分ける。

---

## 最終検証と Git

完了前に最低限次を確認する。

```bash
python3 -m json.tool .raw/.manifest.json >/tmp/manifest-check.json
python3 -m json.tool .raw/videos/<slug>/metadata.json >/tmp/video-metadata-check.json
find .raw/videos/<slug> -path '*/media/video.*' -print
find .raw/videos/<slug>/frames -type f | wc -l
find "wiki/sources/_attachments/<attachment-slug>" -type f | sort
git status --short
```

取り込み完了後、`wiki/` と `.raw/` の非メディアファイルを一括でコミットする。動画・音声バイナリは除外する。

```bash
git add wiki/ .raw/.manifest.json .raw/videos/<slug>/metadata.json \
  .raw/videos/<slug>/transcript.md .raw/videos/<slug>/frames/
git commit -m "wiki: ingest-video | <動画タイトル>"
```

コミットメッセージの形式: `wiki: ingest-video | <動画タイトル>`。タイトルは source ページの `title:` フィールドの値をそのまま使う。`.raw/videos/<slug>/media/` の動画・音声バイナリは staging しない(ファイルが大きく vault に保存しない方針のため)。代表フレーム画像(`.raw/videos/<slug>/frames/`)はコミット対象に含める。git index への書き込みが sandbox で拒否された場合は、承認付きで再実行する。承認も失敗した場合は、作業完了と未コミット理由を報告する。

---

## やってはいけないこと

- `.raw/` の既存ファイルを変更しない。ただし本スキルが新規生成する `.raw/videos/<slug>/` と `.raw/.manifest.json` は対象外。
- `papers/`・`research/`・`structures/`・`notes/` を書き換えない。
- transcript だけで映像を見たことにしない。
- 代表フレームで読めない数値や画面内容を推測しない。
- 自動文字起こしで画面上の正確な数値・固有名を上書きしない。
- source ページ本文から `.raw/` のフレーム画像へ直接リンクしない。必要な画像を `wiki/sources/_attachments/<attachment-slug>/` にコピーし、`![[_attachments/<attachment-slug>/...]]` の Obsidian 画像埋め込みで表示する。
- ログ・hot・manifest の更新を省略しない。カタログは `wiki-catalog.py` で更新する(Read/Edit しない)。
- ロック解放前に final を返さない。
- 動画本体を `.raw/videos/<slug>/` や `wiki/` に保存しない。
- `.raw/videos/<slug>/media/` の動画・音声バイナリをコミット対象に含めない(代表フレーム画像は含めてよい)。
