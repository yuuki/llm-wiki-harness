# wiki/CLAUDE.md — LLM wiki レイヤー運用ガイド

`wiki/` を操作する前に、このファイルと [`meta/conventions.md`](meta/conventions.md) を読む。一般的なリポジトリ運用は vault ルートの `AGENTS.md` または `CLAUDE.md` を参照する。

## 構造

- `.raw/` — 新規ソースの不変原本。ingest は読み取りのみ
- `wiki/` — LLM が生成する横断知識
  - `sources/` / `entities/` / `concepts/` / `questions/` / `asks/` / `briefs/` / `surveys/` / `meta/`
  - `index.md` カタログ / `hot.md` 直近窓 / `log.md` 今月の操作履歴 / `log/YYYY-MM.md` 過去月の操作履歴（`prepend-log` が月替わりに自動で移す） / `overview.md`
- `.vault-meta/` — `mode.json` / `transport.json` / lock / address カウンタ
- `scripts/` — カタログ・解決・抜粋・retrieve・原本取得に加え、矛盾索引・再編纂キュー・再編纂の確定（`recompile-finalize.sh`）・命題再検証・問い節の断定判定（`wiki_questions.py`）・ページグラフ（`graph.json` version 2。辺に `via`）・テーマ塊（`wiki-clusters.py` の `lookup` / `members` だけをスキルから呼ぶ。`.vault-meta/clusters.json` は Read しない）・配役つき近傍（`wiki-related.py`。stdout だけ見る。`.vault-meta/related/` を Read するな）・concept 候補台帳・concept 方言表（`concept-resolve.py`）・論文 ID・論文の文献表台帳（`paper-refs.py`）・entity 名寄せ・関心要約・survey のテーマ推薦と機械検査（`wiki-survey-recommend.py` / `wiki-survey-check.py`）・原本 manifest の 1 件読み書き（`wiki-manifest.py`）・コンテキスト束・機械状態検査

既存の一次ノート（`papers/` や `notes/` など）がある vault では、それらを ingest で書き換えない。wiki からは一方向参照だけにする。MOC への逆リンクは人間が承認したときだけ 1 件単位。

## 鉄則

1. 操作前に `wiki/meta/conventions.md` を読む。標準形式と矛盾したら conventions が優先する。
2. スコープは `.raw/` に置いた新規ソースだけ。既存ノート層は ingest しない。
3. `.raw/` の原本は不変（更新してよいのは `.raw/.manifest.json` だけ）。
4. `wiki/log.md` は先頭に追記する。過去エントリは編集しない。過去月のエントリは `wiki-catalog.py prepend-log` が `wiki/log/YYYY-MM.md` へ移す（移動は編集に当たらない）。`wiki/log/` は手で書かない。
5. 新規の source / entity / concept は `bash scripts/allocate-address.sh` ではなく、`wiki-page-write.py` 経由で採番する（ヘルパー外の採番は失敗する）。
6. ページ書き込みは `wiki-page-write.py` / `wiki-append.py`、または `wiki-lock.sh`。`wiki-catalog.py` は自己 lock するので包まない。`.raw/.manifest.json` は Read も手書きもせず、`wiki-manifest.py get` / `put` で 1 件ずつ扱う。
   - **並行セッションの規則**。(1) `wiki-retrieve-refresh.py --all` の全件再構築は、他セッションが取り込みや再編纂をしている間は走らせない。bm25 のロックは最大 180 秒待つ（`BM25_LOCK_WAIT_SEC`）が、全件再構築が続くと他セッションの差分更新を待たせる。(2) 台帳の更新（`mark`・`claim-audit.py record`・`prepend-log`）を、索引の更新より後ろに置いて `set -e` で繋がない。索引が落ちると台帳が飛ぶ。再編纂の確定は `recompile-finalize.sh` を使う。(3) 確定処理の出力を grep で絞るときは `ERR|WARN|FAILED` を必ず残す。(4) コミットは `git commit -- <パス列挙>` で対象を限定する。自分以外のコミットを amend しない。
7. `index.md` / `_index.md` / `log.md` / `log/*.md` / `hot.md` の全文を ingest / query の入力にしない。発見は `wiki-resolve.py`、本文は `wiki-excerpt.py`、カタログ更新は `wiki-catalog.py`、照会は `retrieve.py`。長い引き継ぎは `wiki-context-pack.py`。lint の最初は `wiki-doctor.py`。
8. **テーマ塊**: id を wiki ページへ書かない。Gap Finder 報告の `#id` と `wiki-clusters.py` の id を突合しない。`.vault-meta/clusters.json` を Read しない。見るなら `wiki-clusters.py lookup` / `members`。画面の Directed Louvain とは別物である。

## skill の分担

| やりたいこと | skill | 出力 |
|---|---|---|
| 論文（PDF 原本ごと） | `wiki-ingest-paper` | `.raw/papers/` + `wiki/{sources,entities,concepts}/` |
| 博士論文・長編サーベイ（章分割） | `wiki-ingest-thesis` | `.raw/theses/<slug>/` + wiki |
| 書籍（章分割） | `wiki-ingest-book` | `.raw/books/<slug>/` + wiki |
| スライド | `wiki-ingest-slides` | `.raw/slides/<slug>/` + wiki |
| 動画 | `wiki-ingest-video` | 文字起こしを補助に wiki 化 |
| 記事など一般ソース | `wiki-ingest` | `.raw/articles/` + wiki |
| 今見ているページの隣（配役つき近傍） | `wiki-related` | stdout のみ。`.vault-meta/related/` は Read しない。問い検索は `wiki-query` |
| 単発の問い・1 ソースの解説・2 対象の差分 | `wiki-query` | 回答 + 標準では `wiki/questions/`（save-first） |
| 既読 1 source への軽い確認・深掘り | `wiki-ask-source` | `wiki/asks/`（1 source = 1 ノート。conventions §14） |
| 既読 1 source の配布用紹介文（Slack 等） | `wiki-brief-source` | `wiki/briefs/`（1 source = 1 ノート。conventions §15）。旧名 `summarize-paper-note` |
| 1 つの命題の判定（「〜は本当か」） | `wiki-thesis` | `wiki/questions/`（`type: thesis`） |
| サーベイのテーマ選定（設問が未定。候補だけ） | `wiki-survey-recommend` | 推薦表 + `wiki/meta/survey-recommend-*.md` |
| 設計空間・文献地図・対象の像 | `wiki-survey` | `wiki/surveys/` |
| ギャップ・閉じなかった命題からの着想 | `wiki-ideate` | 承認後 `research/ideas/`（無いレイヤーなら `$TMPDIR`） |
| 外部読者向け清書 | `wiki-publish` | `notes/<domain>/` |
| ページ衛生 | `wiki-lint` | `wiki/meta/lint-report-*.md`（冒頭に doctor） |
| 構造的間隙の判定 | `wiki-gap` | `wiki/meta/gap-report-*.md` |
| 概念の再編纂・統合・親子化、命題の再検証 | `wiki-refactor` | 対象の concept ページ + `wiki/meta/claim-audit-*.md`。1 ページ = 1 コミット |

テーマが未定なら `wiki-survey-recommend`、設問があるなら `wiki-survey`。母集団が薄いときの `wiki-survey` は `wiki-ingest-*` または上流の `autoresearch` へ差し戻す。命題に触れる source が 2 本未満の `wiki-thesis` も同じ差し戻し（`insufficient`）。どちらも `wiki-query` へは戻さない。開いている 1 ページの隣は `wiki-related`。既読 1 source の軽い確認は `wiki-ask-source`、Slack 等へ貼る紹介文は `wiki-brief-source`。ask / brief は知識の一次ソースではない。

## 概念ページ

各 concept は (1) 定義、(2) 主題節（命題と根拠）、(3) `## 未解決の問い` と `## 未編纂の観察`、(4) 関連リンク、(5) 矛盾は contradiction callout。ingest は受信箱だけを書く。主題節へ畳むのは `wiki-refactor` である。
