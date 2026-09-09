# 章担当 subagent の手順書

`wiki-ingest-book` の従属資料。章担当 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL を渡さない。ウェーブ対ローリング、索引、commit、verify は書かない(オーケストレータの仕事)。

あなたは Obsidian vault（作業ディレクトリ = vault ルート）で、書籍 1 章分を担当する。役割(読み手 / 書き手 / 1〜2 章の一体)は個別プロンプトが指定する。

**3 章以上は読み手と書き手を必ず別起動する。** 同一 subagent に「まず extract を書いてから、章本文を再読せずに書け」と 1 体で回してはならない。ターンをまたいで章本文が常駐文脈に残る。**1〜2 章は 1 体可**(断片 ingest。分離の固定費のほうが大きい)。1〜2 章のときは読み手節の読了と書き手節のページ作成を 1 体で行う。extract は作らない。章本文を全部読んでから wiki を書く。

## 必読

**このファイルの全文を読まない。** 役割に応じて読み手節または書き手節だけを読む。読み手は書き手節を読むな。

- 読み手: [`extract-schema.md`](extract-schema.md) と `wiki/meta/japanese-style.md`。このファイルの「読み手(extract)」節。クロップを任されたときだけ [`figures.md`](figures.md)
- 書き手: `wiki/meta/conventions.md` と `wiki/meta/japanese-style.md`。このファイルの「書き手(write)」節。figures.md のクロップ手順は使わない
- 1〜2 章の一体: 読み手節の読了と書き手節のページ作成。figures.md も読む

本文は日本語・常体。用語は japanese-style に従う(既定はカタカナ、定着した漢語は漢語、略語・固有名詞・wikilink は原語)。

## Bash

毎回 vault ルートから実行する。

```bash
cd "$VAULT_ROOT" && ...
```

相対 `cd` を一度でも打つと、以降の相対パスがずれる。

## git

`git add` / `git commit` / `git stash` / `git checkout` を実行してはならない。確認のための `git status` / `git diff` のみ許可する。

## 触ってはいけないファイル

- 共有カタログ: `wiki/index.md`、`wiki/{sources,entities,concepts}/_index.md`、`wiki/hot.md`、`wiki/log.md`、`wiki/overview.md`。**catalog を subagent が書かない。**
- book entity ページ(オーケストレータのみ)
- `.raw/.manifest.json` と `.raw` の原本
- **extract を `.raw` に置かない。** wiki にも置かない。置き場は briefing と同じスクラッチの `extracts/ch-NN.md`
- **`papers/` `research/` `structures/` `notes/` を書き換えない**

---

## 読み手(extract)

章本文を全部読む。斜め読みしない。長い章は分割して読む。

`<scratch>/extracts/ch-NN.md` を [`extract-schema.md`](extract-schema.md) の必須見出しどおりに書く。**見出しは空でも残す(schema)。** 各箇条書きは短く、locator(印字ページまたは `ch-NN.txt` の行番号)を付ける。本文の逐語コピーは引用節だけ。

**wiki ページは書かない。** `wiki-page-write.py` / `wiki-append.py` を呼ばない。catalog / book entity 禁止。画像の attachment も作らない。ここから下の書き手節は読むな。クロップを任されたときだけ figures.md を読む。

### 完了報告(読み手)

3 行以内。「これから〜する」で終えない。

- extract の絶対パス
- 一行要約
- 図表点数

---

## 書き手(write)

3 章以上の書き手は extract とこの節と書き手用 briefing を読む。**章本文の全文 Read はしない。** locator の確認は grep のみ。パスはオーケストレータが指定した 1 ファイルの該当行だけ。extract が薄いときのピンポイント Read は **1 章あたり最大 2 箇所、各 80 行**。超えるなら自分で広げず、読み手やり直しを報告して止まる。extract が必須見出し欠けなら自分で補完せず、不足を報告して止まる。1〜2 章の一体モードは extract を使わず、章本文を全部読んでからこの節のページ作成へ進む。

### 書き込み

正はヘルパーである。

- 新規ページ: `python3 scripts/wiki-page-write.py --batch spec.json`
- 既存ページ追記: `python3 scripts/wiki-append.py --batch spec.json`

ヘルパーは内部で lock する。**ヘルパーを lock で包まない**(自己デッドロック)。新規を `Write` で作らない。ヘルパーが落ちたら止めて報告する。`Edit` はヘルパーで表現できない既存ページの 1 箇所修正に限り、そのときだけ `wiki-lock.sh acquire` / `release` で囲む。

### 既存ページの発見と部分読み

- resolve は候補をまとめて 1 回。`python3 scripts/wiki-resolve.py concept:"<語1>" concept:"<語2>" entity:"<名1>" --compact`
- excerpt は触るページを並べて 1 回。`python3 scripts/wiki-excerpt.py P1 P2 --tail 15 --budget-tokens 1800`

1 語・1 ページずつ呼ばない。カタログ(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)の全文は Read しない。

### 画像

書き手は briefing の図表表とコンタクトシートと extract の「図表」節だけを使う。章 txt を全文 Read しない。figures.md のクロップ手順は使わない。コンタクトシートを先に Read する。シートと `index.txt` で番号が付いたファイルは Read しない。判断不能セルだけ個別 Read。`individual-reads` は `sheet-*.png` を除く画像 Read の実数。自章のページ範囲だけ。本文参照は除外以外すべて。「## 図表」セクションは作らない。

除外: ページ全体スクショ / 凡例・装飾・著者近影 / ほぼ同一構図の繰り返し / 文脈なし断片。
配置: `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`。
ファイル名に `token`・`secret`・`password`・`credential`・`key` を含めない。

### frontmatter(必須)

章 source に次を必ず入れる。**`publish: false` を省略しない**(著作権。Obsidian Publish から除外)。

- `publish: false`
- `address:`(ヘルパーが採番して挿入)
- バッチ固定の日付(`date` / `created` / `updated` / 先頭の日付タグ)。日付は briefing の値を使う。自分で今日の日付にしない

ほか: `type: source`、`source_type: book`、`related` に book entity、`sources` に章原本。

### 本文の骨格

paper の 9 セクションは使わない。見出しは次だけ。

```markdown
# BookTitle - Chapter N: 章題

> 前: [[@...Chapter N-1...]] | 次: [[@...Chapter N+1...]] | 書籍: [[<書名>]]

## 要約
## 主要概念
## 主要主張
## 実践的指針
## 関連
## 出典
```

- Navigation 行は必須。先頭章は「前:」を、末尾章は「次:」を省略。断片入力は「書籍:」のみ。
- 分量は 60〜120 行を想定(上限 300)。短い章は書き手用 briefing の指示(多くの場合 30〜50 行)に従う。該当のない節は省略してよい。
- 出典に無いことは書かない。主張には章・節番号、無ければ印字ノンブル。`(Source: …)` は使わない。
- 実践的指針は実務書のみ。

### concept / entity

追記は `wiki-append.py` だけ。対象は `## 未編纂の観察` と `## 未解決の問い`。**主題節を書き換えない。** `## 定義` も触らない。単一ソースで言える事実は受信箱に書かない。**新規最大 3 / 更新最大 5。** 溢れたら `Deferred:` を報告する。矛盾は `> [!contradiction]`。`entity_tier`: 共著者・一度きりは `stub`、繰り返し登場するハブは `full`。

entity / concept は作成・更新してよい(ヘルパー経由)。書き込み直前に resolve し直す。HIT / page-write が既存拒否 → append だけ。NONE → page-write だけ。他セッションに上書きされたらマージ復旧せず止める。

### 完了報告(書き手)

10 行以内。次を確定事項として書く。「これから〜する」で終えない。予告で報告を終わらせない。

- 作成・更新したページ(source / entity / concept をすべて。候補として省略しない)
- 章の一行要約
- key_claims
- 気づいた矛盾
