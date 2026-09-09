# 章担当 subagent の手順書(thesis)

`wiki-ingest-thesis` の従属資料。章担当 subagent が読む手順書である。フル SKILL は渡さない。ウェーブ対ローリング、索引、commit、verify は書かない。

あなたは Obsidian vault（作業ディレクトリ = vault ルート）で、博士論文または長編サーベイの 1 章を担当する。役割(読み手 / 書き手 / 1〜2 章の一体)は個別プロンプトが指定する。

## 読み手と書き手の分離

book と同じ 2 段階である。スキーマは [`../../wiki-ingest-book/references/extract-schema.md`](../../wiki-ingest-book/references/extract-schema.md)。

- **3 章以上は読み手と書き手を必ず別起動する。** 同一 subagent に「まず extract を書いてから、章本文を再読せずに書け」と 1 体で回してはならない。
- **1〜2 章は 1 体可。** extract は作らない。章本文を全部読んでから wiki を書く。
- 読み手は章本文を全部読み、`<scratch>/extracts/ch-NN.md` を書く。**見出しは空でも残す(schema)。** wiki は書かない。完了報告は extract パスと一行要約と図表点数だけ。
- **extract を `.raw` に置かない。** wiki にも置かない。置き場は読み手用 briefing と同じスクラッチ(例: `/tmp/thesis-<slug>/extracts/ch-NN.md`)。
- 書き手は extract とこのファイルの書き手節と書き手用 briefing を読む。**章本文の全文 Read はしない。** locator の確認は grep のみ。ピンポイント Read は **1 章あたり最大 2 箇所、各 80 行**。超えるなら読み手やり直し。
- オーケストレータが書き手起動前に `python3 scripts/wiki-extract-check.py --forbid-raw extracts/*.md` をかける。`MISSING` / `QUOTE-HEAVY` / `FORBID-RAW` なら書き手は起動されない。

## 必読

**book brief の全文は読まない。** 役割に応じて book brief の読み手節または書き手節だけを読む。読み手は書き手節を読むな。

- 読み手: book brief の「読み手(extract)」節と [`../../wiki-ingest-book/references/extract-schema.md`](../../wiki-ingest-book/references/extract-schema.md) と `wiki/meta/japanese-style.md`
- 書き手: book brief の「書き手(write)」節と `wiki/meta/conventions.md` と `wiki/meta/japanese-style.md`。このファイルの書き手節
- 1〜2 章の一体: 読み手節の読了と書き手節のページ作成。ベクター図のクロップ [`../../wiki-ingest-paper/references/figures.md`](../../wiki-ingest-paper/references/figures.md)、書籍規模の図表方針 [`../../wiki-ingest-book/references/figures.md`](../../wiki-ingest-book/references/figures.md)

本文は日本語・常体。

## Bash

```bash
cd "$VAULT_ROOT" && ...
```

## git

`git add` / `git commit` / `git stash` / `git checkout` 禁止。確認の `git status` / `git diff` のみ。

## 触ってはいけないファイル

共有カタログ(`wiki/index.md`・各 `_index.md`・`hot.md`・`log.md`・`overview.md`)、**ハブ entity**、`.raw/.manifest.json`。**catalog を subagent が書かない。** **extract を `.raw` に置かない。**

---

## 読み手(extract)

章本文を全部読む。extract を schema どおりに書く。**見出しは空でも残す(schema)。** wiki は書かない。ここから下の書き手節は読むな。

### 完了報告(読み手)

extract パス / 一行要約 / 図表点数。「これから〜する」で終えない。

---

## 書き手(write)

読み手はこれより下を読むな。

### 書き込み

- 新規: `python3 scripts/wiki-page-write.py --batch spec.json`
- 既存追記: `python3 scripts/wiki-append.py --batch spec.json`

**ヘルパーを lock で包まない。** resolve は `--compact` で一括。excerpt は複数ページ 1 回。

### thesis 固有

- **`publish: false` は付けない**(オープンアクセスが主対象。ライセンスが厳しいときだけユーザー確認)。
- `source_type` は博士・修士なら `thesis`、サーベイなら `paper`。
- `related` にハブ entity を必須。対応表にあれば出版論文の `[[@...]]` も足す。
- 論文版と thesis 版で数値・主張が食い違ったら、両ページに `> [!contradiction]`。
- **サーベイの引用文献は entity 化しない。** 節を割いて論じる代表システム・データセットだけ。
- 研究章が 40 ページ超でも、読み手の読了は省略しない。書き手の本文は 300 行上限、深掘りは concept へ。
- 短い章は 30〜50 行。書き手用 briefing の指示に従う。

### 画像

書き手は briefing の図表表とコンタクトシートと extract の「図表」節だけを使う。章 txt を全文 Read しない。figures.md のクロップ手順は使わない。
除外: ページ全体スクショ / 凡例・装飾・著者近影 / ほぼ同一構図の繰り返し / 文脈なし断片。本文参照なしも除外。
配置: `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`。taxonomy 図・比較表は必ず取る。
**`papers/` `research/` `structures/` `notes/` を書き換えない。**

### 本文の骨格

Navigation 行から始める。章の性質で型を選ぶ。

```markdown
> 前: [[@...Chapter N-1...]] | 次: [[@...Chapter N+1...]] | 全体: [[<題名>]]
```

- 研究章: 要約 / 問題設定 / 提案手法 / 実験と結果 / 考察・限界 / 関連
- サーベイ章: 要約 / 分類軸 / 代表手法・システムの比較 / 傾向と未解決課題 / 関連
- 導入・結論・背景: 要約 / 主要概念 / 主要主張 / 関連

出典に無いことは書かない。

### concept

追記は `wiki-append.py` だけ。**主題節を書き換えない。**

### 完了報告(書き手)

作成・更新したページ(すべて) / 章の一行要約 / key_claims / 出版論文との食い違い。「これから〜する」で終えない。
