---
name: wiki-related
description: "今見ている wiki ページの隣を役ごと(contradiction / evidence / definition / cluster)に出す。問い検索ではない。Triggers: /wiki-related, 関連エントリ, このページの関連, このページの隣, 配役近傍, related entries, related pages, neighbors of this page, 今開いているページの近く。開いているノートや @ / linked_note の隣を見たいとき。問い(「X とは何か」「何が分かっているか」)は wiki-query。1 source の確認は wiki-ask-source。"
---

# wiki-related: 開いているページの配役つき近傍

今見ているページの隣を、役ごとに最大 2 件ずつ出す。問いからページを探す検索ではない。`retrieve.py` は呼ばない。契約の正本は [`references/design.md`](references/design.md) である。

> [!important] 鉄則
> - 入口は `python3 scripts/wiki-related.py "<vault相対パス>"` で、引数はページパス 1 つだけである。問い文から探す入口は無い。
> - 見せるのは、その stdout の `roles` にある役と候補だけである。近傍はグラフから機械的に出ており、モデルが役や候補を足すと配役の根拠が消える。
> - `.vault-meta/related/`、`graph.json`、`clusters.json`、`contradictions.json` は Read しない。大きな派生キャッシュで、必要な内容は stdout に出ている。`wiki-clusters.py lookup` / `members` はテーマ塊の所属を返すもので、ページの隣の代わりにならない。
> - 読み取り専用である。wiki 本文と frontmatter を書き換えない(`related:` も更新しない)。
> - 文章は常体。パス・役名は原語のまま。

## 適用範囲

起動するのは、**すでにパスが決まっている 1 ページの隣**を見たいときである。

| 質問の形 | 行き先 |
|---|---|
| 今開いている / `@` したページの関連・隣・配役 | 本スキル |
| 「X とは何か」「何が分かっているか」という問い | `wiki-query`(`retrieve.py`) |
| 1 source の内容確認 | `wiki-ask-source` |
| 「〜は本当か」 | `wiki-thesis` |

迷ったら、種がページパスなら本スキル、種が問い文なら `wiki-query` である。

## 手順

### 1. 種のパスを 1 つに決める

優先順は、ユーザーが書いた vault 相対パス、`@` / linked_note、いま開いている wiki ページである。絶対パスなら vault ルートからの相対に直す。`wiki/` 配下以外は受け取らない。種が決まらなければ、どのページかを聞いて止める。`retrieve.py` や `contradiction-index.py --query` で探した結果を種にしない(それは問い検索であり、`wiki-query` の領分である)。

### 2. コマンドを呼ぶ

```bash
python3 scripts/wiki-related.py "wiki/concepts/Example.md"
```

作業ディレクトリは vault ルートである。

| 終了 | 意味 |
|---|---|
| 0 | グラフ節。stdout の JSON を使う |
| 2 | 使い方の誤り。コマンドを直して一度だけ再実行する |
| 3 | グラフ節ではない(ask / brief など)。`not-a-graph-node` をそのまま伝える。同名の source ページを代わりの種にせず、retrieve にも逃がさない |

### 3. stdout を役ごとに見せる

JSON の `roles` を contradiction → evidence → definition → cluster の順で出す。各候補は `page_path` を `[[wiki/...|表示]]` にし、`why` が空でなければ添える。`omitted` は空の役の理由だけを短く書く。モデルが近傍を足さない。ページ本文を読んで役を作り直さない。
