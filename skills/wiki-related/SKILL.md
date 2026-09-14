---
name: wiki-related
description: "今見ている wiki ページの隣を役ごと(contradiction / evidence / definition / cluster)に出す。問い検索ではない。Triggers: /wiki-related, 関連エントリ, このページの関連, このページの隣, 配役近傍, related entries, related pages, neighbors of this page, 今開いているページの近く。開いているノートや @ / linked_note の隣を見たいとき。問い(「X とは何か」「何が分かっているか」)は wiki-query。1 source の確認は wiki-ask-source。"
---

# wiki-related: 開いているページの配役つき近傍

今見ているページの隣を、役ごとに最大 2 件ずつ出す。問いからページを探す検索ではない。`retrieve.py` は呼ばない。契約の正本は [`references/design.md`](references/design.md) である。

> [!important] 鉄則
> - 入口は `python3 scripts/wiki-related.py "<vault相対パス>"` だけである。`--query` を足すな。
> - 見てよいのはその stdout だけである。`.vault-meta/related/`、`graph.json`、`clusters.json`、`contradictions.json` を Read するな。
> - `wiki-clusters.py lookup` / `members` を近傍の代わりに使うな。
> - wiki 本文と frontmatter を書き換えるな。`related:` を機械更新するな。
> - 役を発明するな。stdout に無い `lexical` / `open_question` を足すな。
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

優先順は、ユーザーが書いた vault 相対パス、`@` / linked_note、いま開いている wiki ページである。絶対パスなら vault ルートからの相対に直す。`wiki/` 配下以外は受け取らない。種が決まらなければ、どのページかを聞いて止める。retrieve で探して種にするな。

### 2. コマンドを呼ぶ

```bash
python3 scripts/wiki-related.py "wiki/concepts/Example.md"
```

作業ディレクトリは vault ルートである。

| 終了 | 意味 |
|---|---|
| 0 | グラフ節。stdout の JSON を使う |
| 2 | 使い方の誤り。コマンドを直して一度だけ再実行する |
| 3 | グラフ節ではない(ask / brief など)。`not-a-graph-node` を伝え、retrieve に逃がさない |

### 3. stdout を役ごとに見せる

JSON の `roles` を contradiction → evidence → definition → cluster の順で出す。各候補は `page_path` を `[[wiki/...|表示]]` にし、`why` が空でなければ添える。`omitted` は空の役の理由だけを短く書く。モデルが近傍を足さない。ページ本文を読んで役を作り直さない。

## やってはいけないこと

- `retrieve.py` や `contradiction-index.py --query` を種にする
- 終了 3 を source の同名ページへ潰す
- キャッシュを暖めるための空打ちを大量に行う
