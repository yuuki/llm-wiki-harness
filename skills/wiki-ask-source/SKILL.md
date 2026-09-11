---
name: wiki-ask-source
description: "既に wiki に取り込み済みの 1 つの source(`wiki/sources/*.md`)について出た軽い確認・深掘り質問を、その場で答えつつ `wiki/asks/` に 1 source = 1 ノートで日付ごとに記録する。source ページと同じ basename の ask ノートを作成または追記し、source の frontmatter と `asks:` で相互リンクする。Triggers: linked_note や `@` で source ノートを渡しつつ「この手法を使う意味は?」「A と B の関係は?」のような理解を深める質問をしたとき、または 'この質問を記録して', 'askに保存して', 'wiki/asksに残して', '/wiki-ask-source' と明示されたとき。単一 source の質問でも、複数ソース横断の説明・引用付き深掘り解説を求めるなら wiki-query、真偽や成立条件を判定したい命題(「〜は本当か」)なら wiki-thesis、まだ wiki に無い source の内容を尋ねているなら該当の wiki-ingest-* が先。Slack 等に貼る配布用の紹介文を作る依頼は wiki-brief-source。"
---

# wiki-ask-source: source への軽い質問を蓄積する

`wiki-query` は複数 source 横断・引用重厚な長編 1 問 1 答を `wiki/questions/` に残す。それは「ちょっと聞いて終わり」の質問には重すぎるため、実際には多くの軽い質問がどこにも残らず消えていた。本 skill は、すでに ingest 済みの 1 つの source について出た軽い質問を、その source に対応する `wiki/asks/` の 1 ノートへ日付ごとに積み増す。規約の正本は [`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §14 であり、本 skill はその手順を実行する。

## 適用範囲

起動するのは、質問が**既に `wiki/sources/` にある 1 つの source の内容を理解するための軽い質問**のときである。

- 「この論文で X をする意味は?」「A と B の関係は?」「なぜこの設計にしたのか?」のような、source 本文の理解を深める確認。
- linked_note や `@` で source ノートが渡された状態での、その場の Q&A。

次は対象ではない。

| 質問の形 | 行き先 |
|---|---|
| 複数 source を横断する説明・比較 | `wiki-query` |
| 「〜は本当か」「〜と言えるか」という命題の判定 | `wiki-thesis` |
| 設計空間・文献母集団の地図を求める長編編纂 | `wiki-survey` |
| まだ wiki に取り込まれていない論文・記事についての質問 | 該当する `wiki-ingest-*` を先に実行する |
| Slack 等に貼る配布用の紹介文 | `wiki-brief-source` |

判断に迷ったら「後で `wiki/questions/` や `wiki/questions/`(thesis)に昇格させる価値があるか」を基準にする。無ければ ask でよい。

## 着手前に読むもの

1. [`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §14(ask ページの規約: 命名衝突回避・frontmatter・昇格条件)。§2(標準 frontmatter)・§5(出典の省略ルール)も参照する。
2. [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md) の役割分担表(ask の位置づけ)。
3. `wiki/meta/japanese-style.md`。本文は常体、コードスイッチングを避ける。

## 手順

### 1. 対象 source を特定する

`linked_note` / `@` / ユーザー指定パスから対象ページを 1 つに決める。`wiki/sources/*.md` かつ frontmatter `type: source` であることを確認する。対象が定まらない、または source がまだ存在しない(ingest 未了)場合は、適切な `wiki-ingest-*` へ差し戻す。

### 2. source を読んで答える

対象 source ページ(必要なら本文の該当節)を `Read` し、その内容に基づいて質問に答える。source に書かれていない一般的な背景知識で補足してよいが、source 固有の主張・数値・手法名を答えの中心に据えるときは source の記述と矛盾しないこと。回答はまず通常のチャット応答として日本語・常体で簡潔に返す(検証済みの内容を先に言い、根拠が薄い部分は明示する)。

### 3. ask ノートのパスを決める

対象 source のファイル名が `wiki/sources/<BASENAME>.md` なら、ask ノートは `wiki/asks/<BASENAME>.md`(**同じ basename、`@` も含めてそのまま**)。folder が違うだけで basename が一致するため、source ⇄ ask の相互参照は必ずパス修飾する(`[[wiki/asks/<BASENAME>|Q&A]]` / `[[wiki/sources/<BASENAME>|...]]`)。ベアリンク `[[<BASENAME>]]` は使わない。

### 4. ask ノートを作成または追記する

**存在しない場合**: 以下の frontmatter・本文で新規作成する。

```yaml
---
type: ask
title: "Q&A: <source の title>"
date: <today> 00:00
created: <today>
updated: <today>
tags:
  - <today: YYYY/MM/DD>
  - ask
status: developing
related: []
sources:
  - "[[wiki/sources/<BASENAME>|<source の短い表示名>]]"
---

# Q&A: <source の title>

Navigation: [[index]] | [[wiki/sources/<BASENAME>|<source の短い表示名>(source)]]

対応する source ページについての軽い質問と回答を、日付ごとに蓄積するノート。深掘りが必要になった問いは `wiki-thesis` や `wiki-query` へ昇格させ、その旨をここに一行残す。

## <today>

**Q. <質問文>**

<回答段落>

## 出典
- [[wiki/sources/<BASENAME>|<source の短い表示名>]]
```

作成は `python3 scripts/wiki-page-write.py "wiki/asks/<BASENAME>.md" --content-file <一時ファイル>` の 1 呼び出しで行う(採番・検証・ロックを内包する)。

**既に存在する場合**: `bash scripts/wiki-lock.sh acquire "wiki/asks/<BASENAME>.md"` でロックしてから編集する。

- 今日の日付の `## YYYY-MM-DD` 節が既にあれば、その節の末尾(`## 出典` の直前)に `**Q. ...**` と回答段落を追記する。
- 無ければ、末尾の `## 出典` 節の直前に新しい `## YYYY-MM-DD` 節を追加する。
- 過去の日付節は編集しない(追記のみ)。
- 編集後、frontmatter の `updated` を今日の日付に更新し、`tags` の末尾に今日の日付タグが無ければ追加する。
- 最後に `bash scripts/wiki-lock.sh release "wiki/asks/<BASENAME>.md"`。

### 5. source 側から ask ノートへリンクする

対象 source の frontmatter に `asks:` フィールドが無ければ、`bash scripts/wiki-lock.sh acquire "wiki/sources/<BASENAME>.md"` → 末尾(`key_claims:` の後など)に `asks: "[[wiki/asks/<BASENAME>|Q&A]]"` を追加 → `release`。既に `asks:` があり同じ ask ノートを指しているなら、source 側は変更しない(質問を積むたびに source の `updated` を動かさない)。

### 6. ログには残さない

`wiki/log.md` は ingest・thesis 判定・survey・maintenance など粒度の大きい操作のための場所である。ask ノートへの 1 問追記のたびに `wiki/log.md` へ記帳しない(token-discipline に反し、log がすぐ埋まる)。ask ノートの追記自体が記録である。

## 既知の環境注意

`scripts/allocate-address.sh` が要求する `flock` コマンドが無いと、新規ページの address 採番が失敗する。その場合は `.vault-meta/address-counter.txt` を読み、その値を `--address c-NNNNNN --no-allocate` として `wiki-page-write.py` に渡し、成功後にカウンタファイルへ `値+1` を書き戻す。恒久対応(`flock` の導入)は人間の作業として明示的に報告する。

## 出力仕様

- チャット応答: 質問への回答(常体、簡潔)。
- ファイル変更: `wiki/asks/<BASENAME>.md` の新規作成または追記 1 件。必要なら `wiki/sources/<BASENAME>.md` の frontmatter に `asks:` を追加(初回のみ)。
- `wiki/log.md` は更新しない(手順 6)。
