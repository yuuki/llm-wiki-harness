# 読み手 subagent の手順書(extract)

`wiki-ingest-book` と `wiki-ingest-thesis` の従属資料。章の読み手 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL も書き手用の手順書も渡さない。ウェーブ対ローリング、索引、commit、verify は書かない(オーケストレータの仕事)。

あなたは Obsidian vault(オーケストレータが個別プロンプトで渡す vault ルートの絶対パス)で、書籍または長編学術文書(博士論文・長編サーベイ)の 1 章を読み、書き手が使う抽出ファイル(extract)を作る。wiki ページは書かない。読み手と書き手を別の subagent に分けるのは、章本文を書き手の常駐文脈に残さないためである。

## 必読

- [`extract-schema.md`](extract-schema.md)(extract の必須見出し)
- `wiki/meta/japanese-style.md`
- オーケストレータが渡す読み手用 briefing(`briefing-extract.md`)
- クロップを任されたときだけ [`figures.md`](figures.md)

extract は日本語・常体で書く。用語は japanese-style に従う(既定はカタカナ、定着した漢語は漢語、略語・固有名詞・wikilink は原語)。

## Bash

毎回 vault ルートから実行する。

```bash
cd <vault ルートの絶対パス> && ...
```

相対 `cd` を一度でも打つと、以降の相対パスがずれる。

## git

`git add` / `git commit` / `git stash` / `git checkout` を実行してはならない。確認のための `git status` / `git diff` のみ許可する。

## 触ってはいけないファイル

- wiki ページ全般。`wiki-page-write.py` / `wiki-append.py` を呼ばない。画像の attachment も作らない
- 共有カタログ: `wiki/index.md`、`wiki/{sources,entities,concepts}/_index.md`、`wiki/hot.md`、`wiki/log.md`、`wiki/overview.md`
- ハブ entity ページ(書籍の book entity、thesis / survey のハブ entity。オーケストレータのみが書く)
- `.raw/.manifest.json` と `.raw` の原本
- `papers/` `research/` `structures/` `notes/`

**extract を `.raw` に置かない。** wiki にも置かない。置き場は briefing と同じスクラッチの `extracts/ch-NN.md` である。

## 読み方と extract

章本文(`chapters/ch-NN.txt`)を全部読む。斜め読みしない。長い章は分割して読むが、省略しない。

`<scratch>/extracts/ch-NN.md` を [`extract-schema.md`](extract-schema.md) の必須見出しどおりに書く。**見出しは空でも残す(schema)。** 各箇条書きは短く、locator(印字ページまたは `ch-NN.txt` の行番号)を付ける。本文の逐語コピーは引用節だけにする。

## 完了報告

3 行以内。「これから〜する」で終えない。

- extract の絶対パス
- 一行要約
- 図表点数
