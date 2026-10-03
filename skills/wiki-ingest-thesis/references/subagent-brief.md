# 書き手 subagent の手順書(thesis)

`wiki-ingest-thesis` の従属資料。章の書き手 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL を渡さない。ウェーブ対ローリング、索引、commit、verify は書かない(オーケストレータの仕事)。読み手(extract 担当)の手順書は book と共通の [`../../wiki-ingest-book/references/reader-brief.md`](../../wiki-ingest-book/references/reader-brief.md) である。

あなたは Obsidian vault(オーケストレータが個別プロンプトで渡す vault ルートの絶対パス)で、博士論文または長編サーベイの 1 章分の wiki ページを書く。役割(3 章以上の書き手 / 1〜2 章の一体)は個別プロンプトが指定する。

- **3 章以上の書き手**: 読み手が作った extract(`<scratch>/extracts/ch-NN.md`)とこの手順書と書き手用 briefing を読む。**章本文の全文 Read はしない。** 章本文を書き手の常駐文脈に残さないために、読み手と書き手は別起動になっている。locator の確認は grep のみ。ピンポイント Read は **1 章あたり最大 2 箇所、各 80 行**。超えるなら自分で広げず、読み手やり直しを報告して止まる。extract が必須見出し欠けなら自分で補完せず、不足を報告して止まる。
- **1〜2 章の一体**: extract は作らない。章本文を全部読んでから、この手順書のページ作成へ進む。ベクター図のクロップは [`../../wiki-ingest-paper/references/figures.md`](../../wiki-ingest-paper/references/figures.md)、書籍規模の図表方針は [`../../wiki-ingest-book/references/figures.md`](../../wiki-ingest-book/references/figures.md) を読む。

## 必読

- `wiki/meta/conventions.md` と `wiki/meta/japanese-style.md`
- 書き手用 briefing(対応表と引用文献 entity の規律を含む)

本文は日本語・常体。用語は japanese-style に従う。

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
- **ハブ entity**(オーケストレータのみ)
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

書き手は briefing の図表表とコンタクトシートと extract の「図表」節だけを使う。章 txt を全文 Read しない。3 章以上の書き手は figures.md のクロップ手順を使わない。コンタクトシートを先に Read する。シートと `index.txt` で番号が付いたファイルは Read しない。判断不能セルだけ個別 Read。自章のページ範囲だけ。「## 図表」セクションは作らず、記述の近傍に埋め込む。

本文が参照している図表は、次の除外に当たるもの以外すべて埋め込む。taxonomy 図・比較表・年表は必ず取る。

- 本文からの参照が一切ない図表(装飾画像・著者近影・ロゴ)
- 凡例のみの画像、ページ全体のスクリーンショット相当の画像
- コードブロックで代替できるコード画像(Markdown コードブロックとして転記する)
- 付録でのみ参照される図表(その章の主張に直結するなら含めてよい)
- ほぼ同一構図が 5 点以上続く繰り返し(代表 1〜2 点。省略数を報告に書く)

配置: `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`。

## frontmatter(必須)

- `address:`(ヘルパーが採番して挿入)
- バッチ固定の日付(`date` / `created` / `updated` / 先頭の日付タグ)。日付は briefing の値を使う。自分で今日の日付にしない
- `type: source`。`source_type` は博士・修士論文なら `thesis`、サーベイなら `paper`
- `related` にハブ entity を必ず入れる。対応表にあれば、対応する出版論文の `[[@...]]` も足す
- `sources` に章原本(`.raw/theses/<slug>/chapters/ch-NN.txt`)
- **`publish:` は書かない。** 対象はオープンアクセスの学術文書である。ライセンスが厳しい文書だけ、オーケストレータがユーザーに確認して briefing で指示する

## 本文の骨格

先頭行に H1 を 1 つだけ置き(frontmatter の `title` と同じ文字列)、続けて Navigation 行を置く。章の性質で型を選ぶ。迷ったら研究章型に寄せる。

```markdown
# <frontmatter の title と同じ文字列>

> 前: [[@...Chapter N-1...]] | 次: [[@...Chapter N+1...]] | 全体: [[<題名>]]
```

- 研究章: 要約 / 問題設定 / 提案手法 / 実験と結果 / 考察・限界 / 関連
- サーベイ章: 要約 / 分類軸 / 代表手法・システムの比較 / 傾向と未解決課題 / 関連
- 導入・結論・背景: 要約 / 主要概念 / 主要主張 / 関連

出典に無いことは書かない。研究章が 40 ページ超でも本文は 300 行を上限にし、深掘りは concept へ逃がす。短い章は書き手用 briefing の指示(多くの場合 30〜50 行)に従う。

論文版と thesis 版で数値・主張が食い違ったら、両ページに `> [!contradiction]` を立てる。

## concept / entity

追記は `wiki-append.py` だけ。対象は `## 未編纂の観察` と `## 未解決の問い`。**主題節を書き換えない。** `## 定義` も触らない。問い節には問いか未解決点だけを書き、出典付きの断定は受信箱へ書く。規約外の節を作らない。単一ソースで言える事実は受信箱に書かない。**新規 3 / 更新 5 の上限は文書全体の枠で、章ごとの枠ではない。** 新規 concept は書き手用 briefing が名指ししたものだけ作る。名指しの無い候補は作らず `Deferred:` として報告する。既存 concept の更新は briefing が挙げたページに留める。矛盾は `> [!contradiction]`。

**サーベイの引用文献は entity 化しない。** entity にするのは、節を割いて論じる代表システム・データセットだけである。`entity_tier`: 一度きりの言及は `stub`、繰り返し登場するものは `full`。

書き込み直前に resolve し直す。HIT / page-write が既存拒否 → append だけ。NONE → page-write だけ。他セッションに上書きされたらマージ復旧せず止める。

## 数値の照合

extract から写した数値・節番号にも取り違えが混じる。書いたあと機械照合する。

```bash
# 章 source を書いた直後。本文と key_claims の数値を章の抽出テキスト(と toc.txt)に照らす
python3 scripts/claim-audit.py numbers --source-page "wiki/sources/@<章ページ>.md"
# concept への追記の前。append の spec をファイルへ書いてから、観察と問いの数値を、その行が引く出典に照らす
python3 scripts/claim-audit.py numbers --spec "$TMPDIR/appends-<slug>-ch<NN>.json"
```

- 終了 1 の `missing` は、1 件ずつ `rg -n -C2 '<数値>' ".raw/theses/<slug>/chapters/ch-<NN>.txt" | head -20` で原文を引き、原文の値に直すか消す。この `rg` はピンポイント Read の 2 箇所に数えない。章 txt を全文 Read しない。PDF は開かない。
- 論文版と thesis 版で数値が違うときは、どちらの値かを行が引く出典で分ける(論文版の値なら論文版の `[[@...]]` を引く)。
- 和・比のような導出値は導出と分かる書き方にする。`caption_missing`(図から読んだ数値)は残してよい。

## 完了報告

次を確定事項として書く。「これから〜する」で終えない。

- 作成・更新したページ(source / entity / concept をすべて)
- 章の一行要約
- key_claims
- 出版論文との食い違い
- `numbers: source missing=N→0 / spec missing=M→0`
