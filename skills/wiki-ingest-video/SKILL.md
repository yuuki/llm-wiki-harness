---
name: wiki-ingest-video
description: "Video-specialized wiki ingest. Use when the source is a standalone video URL or local video file: YouTube, conference recordings, demos, lectures, and talks without a slide PDF as the primary source. Keeps the transcript and representative frames under `.raw/videos/<slug>/` (never the video itself) and files the source into `wiki/sources/`, `wiki/entities/`, `wiki/concepts/`. A talk whose primary source is a slide PDF goes to wiki-ingest-slides. Triggers on: 'この動画を wiki に', 'YouTube を ingest', '録画を wiki 化', 'video source を ingest'."
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
2. **公式ページ**: タイトル、登壇者、所属、概要、イベント情報。動画の概要欄(`metadata.json` の題名・投稿者・公開日・チャプター)もここに準じる。
3. **動画 transcript / 音声**: 口頭説明、背景、設計判断、質疑応答。
4. **自動抽出メタデータ**: URL、ファイル名、長さ、推定タイトル。推定として扱う。

映像と transcript が食い違う場合、画面に表示された数値・固有名・コードは映像を正とする。音声だけで述べられた解釈や背景は transcript を根拠にしてよい。

---

## Step 0: 動画素材を準備する

音声抽出・代表フレーム抽出・文字起こしはヘルパーに任せる。動画本体はローカル入力を直接参照するか、URL 入力では一時ディレクトリにだけ取得し、派生物を作った後に破棄する。

```bash
bash scripts/prepare-video-source.sh "https://www.youtube.com/watch?v=..."
bash scripts/prepare-video-source.sh "/path/to/talk.mp4" "optional-slug"
bash scripts/prepare-video-source.sh --transcript "/path/to/transcript.vtt" "/path/to/talk.mp4" "optional-slug"
bash scripts/prepare-video-source.sh --candidate-interval 10 "https://www.youtube.com/watch?v=..."   # スライド送りが速い講演
bash scripts/prepare-video-source.sh --force "https://www.youtube.com/watch?v=..." "optional-slug"
```

ヘルパーの出力(key=value):

```text
video=
audio=.raw/videos/<slug>/audio.m4a
transcript=.raw/videos/<slug>/transcript.md
transcript_source=manual-subtitles:en        # manual-subtitles:<lang> | auto-subtitles:<lang> | whisper | provided | none
frames_dir=.raw/videos/<slug>/frames
frames=30
candidates_dir=$TMPDIR/prepare-video-source.XXXX/candidates      # vault 外。cand-HHMMSS.jpg
candidates=189
contact_sheets=$TMPDIR/prepare-video-source.XXXX/contact-sheets  # sheet-NNN.jpg と sheets.tsv
duration=3787.06
title=
url=https://www.youtube.com/watch?v=...      # 追跡用パラメータを落とした正規形
metadata=.raw/videos/<slug>/metadata.json
slug=<slug>
```

ヘルパーが行うこと:
1. ローカル動画はコピーしない。URL 入力は **メタデータ → 字幕 → 動画の 3 段に分けて** `yt-dlp` を呼ぶ。1 段が失敗しても他の段は続く。
   - メタデータ: `--dump-single-json` で題名・投稿者・公開日・長さ・チャプター・字幕の一覧を得る。
   - 字幕: **動画の原語の手動字幕**を最優先し、無ければ自動字幕の原語(`<lang>-orig`)を使う。言語コードは完全一致で 1 つだけ指定する。翻訳字幕(例: 英語講演の日本語字幕)は固有名や数値の出典にならないので選ばない。VTT は 1 分単位の時刻つき `transcript.md` に変換する。
   - 動画: 字幕は取らずに 480p 以下の mp4 を取得する。既定の client が失敗したら `player_client=android`、次に `web_embedded` へ切り替える。成功した client は `download_status` に残る。
2. `ffmpeg` がある場合は音声を `.raw/videos/<slug>/audio.m4a` に抽出する。
3. 代表フレーム: 2 分に 1 枚を目安に 6〜30 枚を、各区間の中点から `frames/frame-001.jpg` 形式で抽出する。
4. 候補フレーム: 一定間隔(既定 20 秒)の原寸フレームを **vault 外の作業ディレクトリ**に `cand-HHMMSS.jpg` として残す。16 枚ずつの 4x4 コンタクトシート `sheet-NNN.jpg` も作り、シート内の位置(左上から行優先で 1〜16)と候補ファイルの対応を `sheets.tsv` に書く。動画本体は削除する。
5. 字幕が得られず `whisper` がある場合だけ文字起こしする。`--transcript <file>` の指定はこれらに優先する(`.vtt` / `.srt` なら時刻つきに変換する)。
6. `metadata.json` に `slug`、`url`、`title`、`uploader`、`upload_date`、`video`(常に空文字)、`audio`、`transcript`、`transcript_source`、`frames_dir`、`frames`、`frame_times`(frame 名 → 時刻)、`duration`、`chapters`、`download_status` を保存する。

任意依存:
- `yt-dlp`: 動画 URL から字幕や一時動画を取得する。
- `ffmpeg` / `ffprobe`: 音声抽出、長さ取得、代表フレーム・候補フレーム・コンタクトシートの作成。
- `whisper` または `whisper-cpp`: 文字起こし。`whisper-cpp` はモデル指定が環境依存なので自動実行しない。

依存が無い場合は失敗扱いにしない。保存・抽出できた範囲で進め、不足は source ページの `## 限界・不確実点` とチャット報告に残す。

既存 `.raw/videos/<slug>/` にファイルがある場合、ヘルパーは既定で停止する。`.raw/` 原本の不変性を守るためだ。意図的に再生成する場合だけ `--force` を使う。`--force` は本ヘルパーが生成する `audio.m4a`、`transcript.md`、`frames/frame-*.jpg`、`source.url`、`metadata.json` を置き換え、旧形式の `media/video.*` があれば削除する。

> [!note] ネットワークの許可
> サンドボックスの自動モードでは、ヘルパーの呼び出しに `www.youtube.com`・`*.youtube.com`・`*.googlevideo.com`・`*.ytimg.com`・`youtubei.googleapis.com` を許可ドメインとして添える。

### helper が URL-only で終わった場合

`download_status=url-only` は、3 つの client すべてで動画を取れなかったことを示す。字幕とメタデータは別段なので残っていることが多い。すぐに諦めず、同じ `.raw/videos/<slug>/` を手動で補完する。実行したコマンドと取得可否は最終報告に残す。

推奨順:
1. 字幕が無ければ `yt-dlp --list-subs <url>` で確認し、`--skip-download --write-subs`(または `--write-auto-subs`)`--sub-langs <完全一致のコード> --ignore-errors` で VTT を取る。`--sub-langs "en.*"` のような正規表現は自動翻訳字幕(`en-ja` 等)まで拾って HTTP 429 を受け、yt-dlp 全体が中断するので使わない。
2. 動画は字幕と別の呼び出しで取る: `-f "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]" --merge-output-format mp4`。client を `ios` などへ変えるのも手である。取得先は `"${TMPDIR:-/tmp}"` 配下にし、`.raw/` には置かない。
3. 取得した動画をローカル入力としてヘルパーに渡し直せば、音声・フレーム・候補・シートがそろう: `bash scripts/prepare-video-source.sh --force --transcript <取得した.vtt> <tmp の mp4> <slug>`。そのあと `source.url` と `metadata.json` の `url` / `download_status` を手で直す(例: `manual-fallback: 字幕と動画を別呼び出しで取得 -> ヘルパーにローカル入力で再投入 -> 動画本体は破棄`)。終わったら一時の動画を消す。

この手動補完でも `.raw/` の不変性を守る。既存の別ソース素材を上書きしない。

> [!note] 手動で ffmpeg を使うときの注意
> - 一時ディレクトリは `"${TMPDIR:-/tmp}"` 配下にする。`/tmp` 直下や `/var/folders/...` はサンドボックスで書き込みを拒否されることがある。
> - ffmpeg 7 以降では `-vsync` は廃止された。`-fps_mode vfr` を使う。
> - 1 枚だけ書く出力(タイル画像など)には `-frames:v 1 -update 1` を付ける。
> - `drawtext` フィルタは入っていないビルドがある。画像に時刻を焼き込まず、ファイル名に時刻を入れて対応させる。
> - シーン検出(`select='gt(scene,0.35)'`)は、話者とスライドを切り替える講演映像では取りこぼしが多い。一定間隔の候補とコンタクトシートの方が確実である。

---

## Step 1: フレームと transcript を読む

### 1a. 代表フレームとコンタクトシートでスライドを拾う

`.raw/videos/<slug>/frames/frame-*.jpg` を全て確認する。講演動画では、等間隔の代表フレームの大半が話者の映像になり、重要なスライドを取りこぼしやすい。そこで `contact_sheets` の `sheet-*.jpg` を全て見て、次を拾う:

- デモ画面、UI、ターミナル、ログ、コード、設定値。
- グラフ、表、図、ホワイトボード、画面上の数値・固有名。
- 章タイトル、まとめ画面、Q&A の画面表示。

`metadata.json` の `chapters` がある場合は、章の開始時刻を手掛かりに見るシートを絞ってよい。

シートで見つけた位置は `sheets.tsv` で候補ファイルに引く。**シート上の見た目の位置から番号を暗算しない**(隣の frame を添付画像に取り違えやすい)。候補は必ず原寸で開いて中身を確かめてから、`frames/` に追加する:

```bash
awk -F'\t' '$1=="sheet-006.jpg" && $2==5' "$CONTACT_SHEETS/sheets.tsv"   # → cand-003320.jpg
python3 scripts/video-add-frames.py <slug> "$CANDIDATES_DIR/cand-003320.jpg" "$CANDIDATES_DIR/cand-001100.jpg"
# 出力: frame-031	0:33:20 / frame-032	0:11:00(metadata.json の frames と frame_times も更新される)
```

`video-add-frames.py` は続き番号の `frame-NNN.jpg` として複写し、既存の frame は書き換えない。表や数値の多いスライドは、原寸でも読めない文字を推測しない。候補の作業ディレクトリは vault 外にあり、取り込みが終われば消してよい。

### 1b. transcript を読む

`transcript.md` がある場合は全体を読む。transcript は口頭説明の根拠である:

- 動機、背景、設計判断、失敗例、制約、未解決課題。
- 映像に映る内容を登壇者がどう解釈したか。
- Q&A。質問者名・所属は明示されている場合だけ記録する。

`transcript_source` を確かめる。`auto-subtitles:*` と `whisper` は固有名と数値を誤りやすいので、映像か公式ページで裏を取れないものは不確実として扱う。手動字幕でも、発話の言い間違い(画面の数値と食い違う口頭の数値)は起こる。そのときは画面を正とし、食い違いを `## 限界・不確実点` に書く。

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

コピーした画像は 1 枚ずつ開き、本文のキャプションと中身が一致するか確かめる。キャプションの時刻は `metadata.json` の `frame_times` から取る。

---

## Step 2: 再取り込み判定

`.raw/.manifest.json` のキー `.raw/videos/<slug>` の記録と比較する。URL 入力は `url` を使う。ローカル動画入力は、vault 外の元ファイルから取得前に計算した `source_hash` を使う。manifest は 2 MB を超え、他セッションも書き込むので、Read したり手で書き戻したりしない。`scripts/wiki-manifest.py` が、ロックの内側で 1 件ずつ読み書きする。

```bash
md5 -q /path/to/talk.mp4 2>/dev/null || md5sum /path/to/talk.mp4 | cut -d' ' -f1
python3 scripts/wiki-manifest.py get ".raw/videos/<slug>"     # 終了 3 = 未登録
```

- `source_hash` または URL が manifest と一致: 「取り込み済み(未変更)。`force` で再取り込み」と報告してスキップ。
- 未登録/異なる/force: 取り込みを実行。
- 完了後に次の 1 呼び出しで記録する(Step 5)。エントリは manifest の最上位に入る。`put` は既存エントリに欄を上書きで足す。

```bash
python3 scripts/wiki-manifest.py put ".raw/videos/<slug>" --json '{"source_hash": "<md5>", "url": "https://www.youtube.com/watch?v=...", "transcript_hash": "<md5>", "frames_count": 12, "ingested_at": "YYYY-MM-DD", "pages_created": ["wiki/sources/@2026__YouTube__Title.md"], "pages_updated": ["wiki/concepts/Example.md"]}'
```

URL-only の場合は `source_hash` と `transcript_hash` を空文字にし、`url` と `frames_count: 0` を記録する。

---

## Step 3: source ページを作る

ファイル名は conventions に従い、`wiki/sources/@YYYY__SOURCE__Title.md` とする。`SOURCE` は YouTube、イベント名、企業名、会議名など、動画の公開元として読者が理解できる略号を使う。`date_published` と `author` は `metadata.json` の `upload_date`・`uploader` と概要欄で裏を取る。

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
  - "[[.raw/videos/<slug>/source.url]]"
source_type: video
author: ""
date_published: YYYY-MM-DD
url: ""
confidence: medium
key_claims:
  - "動画または transcript に遡及できる中心主張"
---
```

実ファイルが存在するものだけを `sources:` に残す。動画ファイルへの wikilink は書かない。

本文の基本形。先頭行に H1 を 1 つだけ置く(frontmatter の `title` と同じ文字列)。H1 が無いと `wiki-page-write.py` が拒否する:

```markdown
# {frontmatter の title と同じ文字列}

## 概要
動画全体の要約を 2-3 文で書く。

## 主要メッセージ
- 動画が伝える中心主張。映像・transcript の根拠層を添える。

## 映像で確認できる重要点
- ![[_attachments/<attachment-slug>/frame-003.jpg]]
  frame-003(12:34)のデモ画面では、...
- ![[_attachments/<attachment-slug>/frame-007.jpg]]
  frame-007(25:10)のグラフでは、...

## 口頭説明・補足
transcript がある場合だけ作る。背景・設計判断・補足説明を書く。

## Q&A
transcript に質疑がある場合だけ作る。

## 概念・実体への接続
- 関連 entity / concept への wikilink。

## 限界・不確実点
- 未取得動画、代表フレームなし、transcript なし、音声不鮮明、口頭と画面の食い違い等。
```

`## 時系列メモ` は作らない。長い動画でも全体を時刻順に再要約せず、重要な画面は `## 映像で確認できる重要点` に代表フレームへの Obsidian 画像埋め込み付きで置き、口頭説明は `## 口頭説明・補足` に論点単位でまとめる。フレーム画像は `.raw/` ではなく `wiki/sources/_attachments/<attachment-slug>/` へコピーした画像を指す。`wiki/sources/` 配下の source ページからは `![[_attachments/<attachment-slug>/frame-001.jpg]]` の形を使う。同じ内容を `## 主要メッセージ` と `## 口頭説明・補足` に重ねて書かない。

新規ページ(source・entity・concept)は本文を一時ファイルに書き、`wiki-page-write.py` で作成する。address の採番・検証・ロックを 1 回で行う(wiki/CLAUDE.md 鉄則 7)。

```bash
python3 scripts/wiki-page-write.py "wiki/sources/@YYYY__SOURCE__Title.md" --content-file "$TMPDIR/source.md"
```

---

## Step 4: entity / concept ページを更新する

登壇者、組織、製品、システム、リポジトリ、データセットを `wiki/entities/` に作成・更新する。既存の有無は名前解決で確認する。登場する entity 名と concept 候補を 1 回の呼び出しでまとめて解決する(1 名ずつ呼ばない。呼び出し 1 回が常駐文脈 1 回分の費用になる): `python3 scripts/wiki-resolve.py entity:"<登壇者>" entity:"<所属>" concept:"<候補>" --compact`。`HIT` なら更新、`NONE` なら新規作成。**書き込み直前に、作る予定のページをもう一度 resolve する**(並行セッションが先に作っていることがある)。HIT、または `wiki-page-write.py` が既存ファイルで拒否したら `wiki-append.py` だけを使い、上書きしない。**共著者・一度きりの言及は `entity_tier: stub`**(frontmatter + 所属/役割 2〜3 行)。新規 entity も `wiki-page-write.py` で作る。原名のファイル名が `books/`・`notes/` の既存ノートと衝突しないかは `find . -path ./.git -prune -o -iname "<名>.md" -print` で確かめる(conventions §9 ルール 7)。

concept ページは、動画が扱う重要概念を `wiki/concepts/<原名>.md` に育てる。既存 concept は上の一括 resolve で確認し、更新時は `wiki-excerpt.py --tail 15 --budget-tokens 1800` で必要節だけ読む。**1 回の取り込みで concept は新規最大 3、更新最大 5。** 溢れは Step 5 の log に `Deferred:` として残す。台帳にも積む: `python3 scripts/concept-candidates.py add --name <候補> --source "[[@<今回の source>]]" --reason "上限超過"`。`wiki-resolve.py` が `ledger:<名>(<k> docs, pending)` を返した候補は既に保留中なので同じコマンドで言及を足し、`ready`(2 文書以上)かつ resolve の出力に `dialect-gate` が無く、`is_create_blocked` でもないときだけ今回の新規枠で優先して新設し `promote` する(conventions §12 ルール 5)。resolve が concept を見つけず `dialect-gate`(`--compact` では `dialect-gate:<n> pages`)を返した候補は新設しない。観察は source に留め、`concept-candidates.py add --reason "dialect-gate"` で台帳に積む。研究関心プロファイル(`wiki-profile.py`)へのマッチ度は新設・保留の判断に使わない(conventions §12 ルール 3)。上限内で触れた concept だけ `## 未編纂の観察`(旧名 `## 横断的知見` が残るページではそこ。同じ受信箱)と `## 未解決の問い` を更新する。主題節と `## 定義` は ingest から書き換えない(conventions §8)。追記の前に `wiki-excerpt.py --outline` で既存の命題を見て、補強・反証する観察なら冒頭に `[節名]` を付ける。単一動画だけで言える事実は source ページに置き、複数ソースの突き合わせで見えた観察だけを受信箱に入れる。

既存ページの更新は `wiki-append.py` で行う。受信箱と問いへの追記、frontmatter の `sources` への今回の source の追加、`updated` と日付タグの更新が同じ呼び出しで入る。

矛盾があれば黙って上書きせず `> [!contradiction]` callout を新旧両方に立てる。

---

## Step 5: 索引・hot・log・manifest を更新する

source/entity/concept を書き終えたら、**カタログファイル(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)を Read/Edit しない。** `wiki-catalog.py` で追記する(自己 lock。カタログに `wiki-lock.sh` を二重に取らない):

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-video | 動画タイトル
- Source: URL またはローカル入力から計算した source hash
- Transcript: `.raw/videos/<slug>/transcript.md`(transcript_source の値)または none
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
  --file wiki/concepts/_index.md --text "$(cat <<'EOF'
### YYYY-MM-DD ingest-video | タイトル
- 新規 concept: [[A]]
EOF
)"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/concepts/_index.md \
  --section "現行コンセプトカタログ" \
  --line "- [[A]]"

python3 scripts/wiki-catalog.py prepend-master --text "$(cat <<'EOF'
### YYYY-MM-DD ingest-video | タイトル
- Source: [[@...]]
EOF
)"
```

複数行の `--text` は必ず heredoc で渡す。二重引用符の中の `\n` は改行にならず、カタログに文字のまま入る。

1. 新規 source/entity/concept ごとに、該当 `_index.md` へ `prepend-changelog` + `add-catalog-line`。節名は `wiki/sources/_index.md` が `現行ソースカタログ`、`wiki/entities/_index.md` が `Person` / `Organization` / `Product / System`、`wiki/concepts/_index.md` が `現行コンセプトカタログ`。
2. 大局が変わったら `wiki/overview.md`(Read/Edit 可)。
3. `.raw/.manifest.json` に `{source_hash, url, transcript_hash, frames_count, ingested_at, pages_created, pages_updated}` を `wiki-manifest.py put` で記録する(Step 2)。
4. **BM25 索引の差分更新**(commit 前に必須):
   ```bash
   python3 scripts/wiki-retrieve-refresh.py --pages wiki/sources/@... wiki/entities/... wiki/concepts/... --no-llm
   ```

---

## ロック手順

書き込みは `wiki-page-write.py`(新規)と `wiki-append.py`(既存)が内部でロックを取る。ヘルパー呼び出しを `wiki-lock.sh` で包まない(自己デッドロック)。rc=75 なら 2 秒待って同じヘルパーを 1 回だけ再試行し、なお取れなければ `wiki-catalog.py prepend-log` に skipped lock を残す。`wiki-lock.sh acquire` / `release` で囲むのは、ヘルパーで表現できない既存ページの 1 箇所 `Edit` だけである。

シェル環境によって `bash`、`dirname`、`sha1sum` などの探索に失敗することがある。ロック操作が PATH 起因で失敗したら、迂回せず vault root と標準 PATH を明示して再実行する。

```bash
/usr/bin/env PATH=/opt/homebrew/bin:/bin:/usr/bin:/usr/sbin:/sbin \
  WIKI_LOCK_VAULT="$PWD" \
  /bin/bash scripts/wiki-lock.sh acquire wiki/concepts/Example.md
```

複数ページへの追記は、手動ロックを並べずに `wiki-append.py --batch` に任せる(ページごとのロックはヘルパーが取る)。手動で取ったロックは、編集結果にかかわらず `release` し、`bash scripts/wiki-lock.sh list` が空であることを確認してから報告する。

---

## 出典検査(報告はチャットへ、ノート本文に書かない)

書き終えたら、すべての主張を映像フレーム・公式ページ・transcript・動画メタデータのいずれかに遡及する。検査結果はチャット返信で報告し、source ページ本文に「検証パス」節を書かない。

- 画面に表示された数値・固有名・コードは映像フレームで裏取りする。
- transcript-only の主張は口頭説明由来だと分かるようにする。
- 自動生成 transcript の固有名・数値は、映像か公式ページで裏取りできなければ不確実にする。
- 口頭の数値が画面と食い違うときは画面を正とし、食い違いを報告する。
- 数値は機械照合もする(conventions §5)。source ページを書いた直後に `python3 scripts/claim-audit.py numbers --source-page "wiki/sources/@YYYY__SOURCE__Title.md"` で、本文の数値を `.raw/videos/<slug>/transcript.md` に照らす。concept への追記は spec をファイルへ書いてから `--spec <spec.json>` で照らす。終了 1 の `missing` は `rg -n -C2 '<数値>'` で transcript を引き、原文の値に直すか消す。画面からしか読めない値は transcript に無いのが普通なので、フレームで確かめたうえで残してよい。transcript が無い動画では照合する対象が無いので省く。
- 報告は ✅ 完全に出典裏付けあり / ⚠️ 不確実・要注記 / ℹ️ 推定 に分ける。

---

## 最終検証と Git

完了前に最低限次を確認する。一時ファイルは `$TMPDIR` に書く(`/tmp` 直下はサンドボックスで拒否されることがある)。

```bash
python3 scripts/wiki-manifest.py get ".raw/videos/<slug>" >/dev/null   # 登録されていること
python3 -m json.tool .raw/videos/<slug>/metadata.json >/dev/null
find .raw/videos/<slug> -path '*/media/*' -print          # 何も出ないこと
find .raw/videos/<slug>/frames -type f | wc -l
find "wiki/sources/_attachments/<attachment-slug>" -type f | sort
python3 scripts/wiki-verify-ingest.py --pages "wiki/sources/@YYYY__SOURCE__Title.md" --attachments-slug <attachment-slug>
git status --short
```

`wiki-verify-ingest.py` は frontmatter の必須項目、H1、wikilink と埋め込みの解決、未使用の attachment を見る。error が 0 になってから stage する。

取り込みで作成・更新したファイルだけを**パスを明示して** staging する。作業ツリーには無関係な未コミットの変更があることが多いので、`git add wiki/` のようにディレクトリごと加えない。

```bash
V=.raw/videos/<slug>
git add .raw/.manifest.json "$V/metadata.json" "$V/transcript.md" "$V/source.url" "$V/frames/" \
  "wiki/sources/@....md" "wiki/sources/_attachments/<attachment-slug>/" \
  "wiki/entities/<新規・更新した entity>.md" "wiki/concepts/<更新した concept>.md" \
  wiki/log.md wiki/hot.md wiki/index.md wiki/sources/_index.md wiki/entities/_index.md wiki/concepts/_index.md
git add -f .vault-meta/concept-candidates.json   # 候補台帳に積んだときだけ
git commit -m "wiki: ingest-video | <動画タイトル>" -- <stage したのと同じパス>
```

- `git commit` にも stage したのと同じパスを渡して対象を限定する(wiki/CLAUDE.md 鉄則 8)。並行セッションが stage した変更を巻き込まないためである。

- コミットメッセージの形式: `wiki: ingest-video | <動画タイトル>`。タイトルは source ページの `title:` フィールドの値をそのまま使う。
- `.vault-meta/` は `.gitignore` の対象だが、`concept-candidates.json` は追跡されている。`-f` を付けないと、`git add` 全体が「ignored」で止まる。
- `audio.m4a` は staging しない(vault に動画・音声バイナリを保存しない方針のため、未追跡のまま残す)。代表フレーム画像(`.raw/videos/<slug>/frames/`)はコミット対象に含める。
- vault が Git LFS を使っていると、サンドボックス内では LFS の一時ファイル書き込みが拒否されて `git add` / `git diff` が失敗することがある。その場合は承認付きでサンドボックスの外で再実行する。承認も失敗した場合は、作業完了と未コミット理由を報告する。

---

## やってはいけないこと

原本・既存ノート・履歴を壊す操作と、この skill の出典の核だけを、ここに再掲する。フレームの選び方や画像の置き場など手順上の注意は各 Step にある。

- **`.raw/` の既存ファイルを変更しない。** 例外は、本スキルが今回生成する `.raw/videos/<slug>/` 配下と `.raw/.manifest.json` の更新だけである。既存の `.raw/videos/<slug>/` を作り直すのは、`--force` で意図して再生成するときに限る。
- **`papers/`・`research/`・`structures/`・`notes/` を書き換えない。**
- **動画本体を `.raw/videos/<slug>/` や `wiki/` に保存しない。** `audio.m4a` などの音声・動画バイナリを commit しない(代表フレーム画像は含めてよい)。大きなバイナリは履歴に入ると戻しにくい。
- **カタログは `wiki-catalog.py` だけで更新する**(Read/Edit しない)。log・hot・manifest の更新を省略しない。
- **手動で取ったロックは、すべて release してから報告する。** ヘルパーを `wiki-lock.sh` で包まない。
- **stage はパスを明示し、commit にも同じパスを渡す。** `git add wiki/` のような広い指定を使わない。
- **画面を正とする。** 読めない値や画面内容を推測しない。自動文字起こしや翻訳字幕で、画面上の数値・固有名を上書きしない。transcript だけで映像を見たことにしない。
