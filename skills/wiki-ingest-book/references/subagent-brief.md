# 書き手 subagent の手順書(book)

`wiki-ingest-book` の従属資料。章の書き手 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL を渡さない。ウェーブ対ローリング、索引、commit、verify は書かない(オーケストレータの仕事)。読み手(extract 担当)の手順書は別ファイルの [`reader-brief.md`](reader-brief.md) である。

あなたは Obsidian vault(オーケストレータが個別プロンプトで渡す vault ルートの絶対パス)で、書籍 1 章分の wiki ページを書く。役割(3 章以上の書き手 / 1〜2 章の一体)は個別プロンプトが指定する。

- **3 章以上の書き手**: 読み手が作った extract とこの手順書と書き手用 briefing(`briefing-write.md`)を読む。**章本文の全文 Read はしない。** 章本文を書き手の常駐文脈に残さないために、読み手と書き手は別起動になっている。locator の確認は grep のみ。パスはオーケストレータが指定した 1 ファイルの該当行だけ。extract が薄いときのピンポイント Read は **1 章あたり最大 2 箇所、各 80 行**。超えるなら自分で広げず、読み手やり直しを報告して止まる。extract が必須見出し欠けなら自分で補完せず、不足を報告して止まる。
- **1〜2 章の一体**(断片 ingest。分離の固定費のほうが大きい): extract は作らない。章本文を全部読んでから、この手順書のページ作成へ進む。[`figures.md`](figures.md) も読む。

## 必読

- `wiki/meta/conventions.md` と `wiki/meta/japanese-style.md`
- 書き手用 briefing(`briefing-write.md`)
- 3 章以上の書き手は figures.md のクロップ手順を使わない

本文は日本語・常体。用語は japanese-style に従う(既定はカタカナ、定着した漢語は漢語、略語・固有名詞・wikilink は原語)。

## Bash

毎回 vault ルートから実行する。

```bash
cd <vault ルートの絶対パス> && ...
```

相対 `cd` を一度でも打つと、以降の相対パスがずれる。

## git

`git add` / `git commit` / `git stash` / `git checkout` を実行してはならない。確認のための `git status` / `git diff` のみ許可する。

## 触ってはいけないファイル

- 共有カタログ: `wiki/index.md`、`wiki/{sources,entities,concepts}/_index.md`、`wiki/hot.md`、`wiki/log.md`、`wiki/overview.md`。**catalog を subagent が書かない。**
- book entity ページ(オーケストレータのみ)
- `.raw/.manifest.json` と `.raw` の原本
- **`papers/` `research/` `structures/` `notes/` を書き換えない**

## 書き込み

正はヘルパーである。

- 新規ページ: `python3 scripts/wiki-page-write.py --batch spec.json`
- 既存ページ追記: `python3 scripts/wiki-append.py --batch spec.json`

ヘルパーは内部で lock する。**ヘルパーを lock で包まない**(自己デッドロック)。新規を `Write` で作らない。ヘルパーが落ちたら止めて報告する。`Edit` はヘルパーで表現できない既存ページの 1 箇所修正に限り、そのときだけ `wiki-lock.sh acquire` / `release` で囲む。

## 既存ページの発見と部分読み

- resolve は候補をまとめて 1 回。`python3 scripts/wiki-resolve.py concept:"<語1>" concept:"<語2>" entity:"<名1>" --compact`
- excerpt は触るページを並べて 1 回。`python3 scripts/wiki-excerpt.py P1 P2 --tail 15 --budget-tokens 1800`

1 語・1 ページずつ呼ばない。カタログ(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`)の全文は Read しない。

## 画像

書き手は briefing の図表表とコンタクトシートと extract の「図表」節だけを使う。章 txt を全文 Read しない。コンタクトシートを先に Read する。シートと `index.txt` で番号が付いたファイルは Read しない。判断不能セルだけ個別 Read。`individual-reads` は `sheet-*.png` を除く画像 Read の実数。自章のページ範囲だけ。本文参照は除外以外すべて。「## 図表」セクションは作らない。

除外: ページ全体スクショ / 凡例・装飾・著者近影 / ほぼ同一構図の繰り返し / 文脈なし断片。
配置: `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`。

## frontmatter(必須)

章 source に次を必ず入れる。**`publish: false` を省略しない**(著作権。Obsidian Publish から除外)。

- `publish: false`
- `address:`(ヘルパーが採番して挿入)
- バッチ固定の日付(`date` / `created` / `updated` / 先頭の日付タグ)。日付は briefing の値を使う。自分で今日の日付にしない

ほか: `type: source`、`source_type: book`、`related` に book entity、`sources` に章原本。

## 本文の骨格

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

## concept / entity

追記は `wiki-append.py` だけ。対象は `## 未編纂の観察` と `## 未解決の問い`。**主題節を書き換えない。** `## 定義` も触らない。問い節には問いか未解決点だけを書き、出典付きの断定は受信箱へ書く。規約外の節(`## 未解決の問い（追記）` など)を作らない。単一ソースで言える事実は受信箱に書かない。**新規 3 / 更新 5 の上限は文書全体の枠で、章ごとの枠ではない。** 新規 concept は書き手用 briefing が名指ししたものだけ作る。名指しの無い候補は作らず `Deferred:` として報告する。既存 concept の更新は briefing が挙げたページに留める。矛盾は `> [!contradiction]`。`entity_tier`: 共著者・一度きりは `stub`、繰り返し登場するハブは `full`。

entity / concept は作成・更新してよい(ヘルパー経由)。書き込み直前に resolve し直す。HIT / page-write が既存拒否 → append だけ。NONE → page-write だけ。他セッションに上書きされたらマージ復旧せず止める。

## 数値の照合

extract から写した数値・章節番号にも取り違えが混じる。書いたあと機械照合する。

```bash
# 章 source を書いた直後。本文と key_claims の数値を章原本の抽出テキスト(と書籍の toc.txt)に照らす
python3 scripts/claim-audit.py numbers --source-page "wiki/sources/@<章ページ>.md"
# concept への追記の前。append の spec をファイルへ書いてから、観察と問いの数値を、その行が引く出典に照らす
python3 scripts/claim-audit.py numbers --spec "$TMPDIR/appends-<slug>-ch<NN>.json"
```

- 終了 1 の `missing` は、1 件ずつ `rg -n -C2 '<数値>' "<章 txt>" | head -20` で原文を引き、原文の値に直すか消す。この `rg` はピンポイント Read の 2 箇所に数えない。章 txt を全文 Read しない。
- 観察が既存 source の数値を述べるなら、その行にその source の wikilink を置く(照合は行が引く出典だけに照らす)。
- 和・比のような導出値は導出と分かる書き方にする。`caption_missing`(図から読んだ数値)は残してよい。
- 抽出テキストが無い章(WARN が出る)は照合を飛ばし、報告にそう書く。

## 完了報告

次を確定事項として書く。「これから〜する」で終えない。予告で報告を終わらせない。

- 作成・更新したページ(source / entity / concept をすべて。候補として省略しない)
- 章の一行要約
- key_claims
- 気づいた矛盾
