# 関連エントリ発見の契約

関連エントリ発見は、今見ているページの隣を役ごとに出す編成である。
問いからページを探す検索は `retrieve.py` のままである。本設計はそれを置き換えない。

残す原則は三つである。BM25 を関連の種にしない。単一関連スコアを出さない。残存母集団が尽きた役だけを空にする。他役が取ったページは、残った役の信号に数えない。

実装は `scripts/wiki-related.py` と `.vault-meta/related/` のキャッシュである。スキルが見てよいのは stdout だけである。

## 1. 非目標

- `--query`、`wiki-resolve.py` を種にすること。
- wiki-query から関連 CLI を呼ぶこと。本ハーネスに `wiki-retrieve` skill は無い（上流）。
- `expand()` を候補切断に使うこと。点数式と `reconstruct_via` だけ借りる。
- `hop_via.kind` が無い辺に kind を発明すること。
- `via.sources` へ source ページ自身を足すこと。
- 役として `lexical` / `open_question` を出すこと。
- frontmatter `related` の機械更新。wiki 本文への機械書き。
- エージェントが `clusters.json` / `graph.json` / `.vault-meta/related/` / `.vault-meta/contradictions.json` を Read すること。

## 2. 入口

```bash
python3 scripts/wiki-related.py "wiki/concepts/Example.md"
```

引数は vault 相対パス 1 つである。`--query` は置かない。
種の判定は `graph["nodes"]` の path 完全一致だけである。
stem や `normalize_page` で同名の source へ潰してはならない。

終了コード:

| コード | とき | stdout |
|---|---|---|
| 0 | `graph["nodes"]` に種がある | §3 の JSON |
| 2 | 使い方の誤り | 無し |
| 3 | 種がグラフ節ではない | `{"seed":"...","roles":{},"omitted":[{"role":"*","reason":"not-a-graph-node"}]}` |

survey（パスが `wiki/surveys/` で始まる、または `nodes[seed].type == survey`）は終了 0 である。
cluster 役は `wiki-clusters` の lookup を呼ばず、`omitted: not-on-backbone` にする。
seed entity（`nodes[seed].type == entity` かつ frontmatter `status: seed`）の cluster は `omitted: seed`。
concept の `status: seed` では cluster を省略しない。
lookup 終了 3 を `not-a-graph-node` に流用しない。

## 3. 出力

```json
{
  "seed": "wiki/concepts/Example.md",
  "roles": {
    "definition": [],
    "evidence": [],
    "contradiction": [],
    "cluster": []
  },
  "omitted": [
    {"role": "contradiction", "reason": "no-callout"}
  ]
}
```

`lexical` と `open_question` のキーは無い。

各候補: `page_path`、`absolute_path`、`role`、`via`（1 件以上）、`why`。
1 hop の `via` は `hop_via(graph, seed, dst, 1)` を改変せず載せる。
2 hop の `via` は `reconstruct_via` である。前駆は `expand` と同じく strength 最大、同点は path 昇順。
`kind` が無いときは付けない。`sources` が空のときは接続行を出さない。候補は落とさない。
`why` はモデルが書かない。`sources` が空でなければ「接続: [[近傍]] ← [[種]]、出典 [[@S]]」。2 hop でも近傍は到達頁であり、中継ではない。空なら `why` は空文字である。
種の type は `nodes[seed].type` を見る。catalog の question へ潰さない。`thesis` はグラフ節として終了 0 である。

役の上限は各 2 件、全体 8 件である。

## 4. 埋め手続き

1. `graph["adj"]` を種から BFS し、距離 2 まで常に取る。`top=N` で切らない。`expand()` は呼ばない。
2. 距離 1 の点数は `w / log2(2 + deg(neighbor))`。`w` は `adj[seed][neighbor]`。
3. 距離 2 の点数は strength 最大の単一路である。複数 mid を合算しない。`strength = w(seed,mid) * w(mid,dst) * HOP_DECAY`（0.5）。点数は `strength / log2(2 + deg(dst))`。前駆 mid は strength 最大、同点は path 昇順。
4. frontmatter の `related` と `sources` をグラフ節へ解決する。解決は `link_basename` のあと、`@` 始まりは `resolve_source_stem`、それ以外は `normalize_page`。表示名（`|` 右側）は使わない。衝突は graph 構築と同じ `by_name.setdefault` 先勝ちである。解決できない名前は捨てる。
5. §5 に従い、各役の母集団を組む。並びは役の安定キーである。
6. 枠を次の順で埋める。contradiction、evidence、definition、cluster。各役 2 枠。
7. 同一ページは contradiction と evidence の同時掲載だけ許す。その他は、先の役が取ったページを後の役がスキップし、枠が残れば母集団の次点を取る。
8. 残存母集団（step 7 のスキップ後にまだ枠へ入れられる頁）が 0 のとき、その役を空配列にし `omitted` を書く。初期母集団が空でなくても、残存が 0 なら空である。他役に出た頁を「信号が残っている」と数えてはならない。

`omitted.reason` の値は次だけである。

| reason | とき |
|---|---|
| `not-a-graph-node` | 終了 3。種が `graph["nodes"]` に無い |
| `not-on-backbone` | survey の cluster |
| `seed` | seed entity の cluster |
| `no-concept-neighbor` | definition の衝突スキップ後の残存が 0。初期母集団 0 も含む |
| `no-source` | evidence の衝突スキップ後の残存が 0。初期母集団 0 も含む |
| `no-callout` | 種に対する callout 0 件 |
| `no-opponent` | callout はあるが相手ページが 0 件 |
| `no-nearby-cluster-peer` | survey / seed entity 以外で、衝突スキップ後の cluster 残存が 0 |

## 5. 役の母集団

| 役 | 母集団 | 並び（安定キー） |
|---|---|---|
| definition | 距離 1 の `type == concept`。2 件未満なら距離 2 の concept を足す | (1) frontmatter `related` に出る順 (2) 点数降順 (3) path 昇順 |
| evidence | 距離 1 の `type == source` と、frontmatter `sources` / `related` が解決した source の和集合。距離 2 は足さない | (1) 種の題名だけを針にする。取り方は frontmatter の `title`、無ければパスの stem。alias は使わない。針の casefold 長が 6 未満ならこのキーは常に偽。6 以上なら相手の basename または title に casefold 部分一致 (2) frontmatter `sources` 次いで `related` に出る順 (3) 点数降順 (4) path 昇順 |
| contradiction | 矛盾索引のうち `host == seed`、または種の stem が `record.pages` の要素と完全一致。部分一致は禁止。`--query` は使わない。相手は、host が種なら `pages` の解決先、`pages` が種なら host | 状態 open を先、path 昇順 |
| cluster | 種と同じ `membership.id` を持ち、BFS 距離が 1 または 2 の concept。種自身を除く。survey と seed entity は母集団を組まない。衝突スキップ後の残存母集団がちょうど 2 件、すべて距離 2、かつ頁の題名集合が塊 label の 2 語と一致するときだけ出さず `no-nearby-cluster-peer` にする | (1) 距離昇順 (2) `in_count` 降順 (3) path 昇順 |

`hop_via.kind` で役を決めてはならない。

矛盾索引はスクリプトが読む。`.vault-meta/contradictions.json` が存在し、その `generated_at` が文字列で `graph.built_at` 以上、かつ `records` が dict の配列ならそれを使う。古い・無い・壊れているときは `contradiction_index.collect(..., vault=root)` する。`root` は種ページの vault であり、import 時の `VAULT_ROOT` ではない。エージェント手順にその JSON を Read と書かない。

## 6. キャッシュ

`.vault-meta/related/<key>.json`。`<key>` はグラフ節の `address` が `c-` + 数字のときだけそれを使う。それ以外は path の sha1 先頭 6 桁に `syn-` を付けたものを使う。
キャッシュを読む・書くのは graph / clusters をディスクから載せ、records を注入していないときだけである。
ファイルは `{topology_sha256, graph_built_at, seed, roles, omitted}` である。stdout は §3 の 3 キーである。`absolute_path` は読むときに `root` で付け直す。
`wiki-retrieve-refresh.py` が graph または clusters を建て直したらディレクトリを全削除する。`wiki-graph.py build` と `wiki-clusters.py build` の成功後も全削除する。

## 7. 問い検索との境界

`retrieve.py` の引数と `--no-graph` のバイト一致は変えない。
wiki-query は `retrieve.py` が第一路のままである。
人がページを開いているときだけ `wiki-related.py` を呼ぶ。エージェント入口は `wiki-related` スキルである。`wiki-query` からこのコマンドを呼ばない。
ingest のテーマ塊同僚は `clusters-for-skills.md` のままである。本設計はそれを置き換えない。
