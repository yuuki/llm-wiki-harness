# スキル世界のテーマ塊 — 設計

エージェントの skill / スクリプトが使う**テーマ塊**の正本を、スキル世界（ファイル走査・stem 解決・`.vault-meta/`）に置く。wiki-lens の画面コミュニティを書き出さない。画面とスキルで分割が食い違っても欠陥ではない。

この文書の対象は「所属をスキルが使う」ことである。構造的間隙（cluster thinness / 共起 / Adamic-Adar / bridge）は対象外である。間隙の正本は当面 Gap Finder の Copy report のままとする。本機能を「wiki-lens クラスタリングの利用」とは呼ばない。

関連: [`design.md`](design.md) §4.2 / §5.6（画面側。本レシピの正本ではない）、[`theme-chunks-algorithm.md`](theme-chunks-algorithm.md)、`scripts/wiki-graph.py`、`skills/wiki-gap/SKILL.md`、`skills/wiki-ingest/SKILL.md`、`templates/wiki-CLAUDE.md`。

## 1. 問題

`domain` frontmatter は分類軸として使えない。wiki-lens はリンク構造からコミュニティを計算するが、結果は Obsidian の RAM にしか無い。スキルから見えるのは次だけである。

- Gap Finder の Copy report（塊の**間隙**。所属マップは無い。報告中の `#id` はプラグイン世代）
- `wiki-graph.py` の hop 近傍（コミュニティではない。共起辺を含む）

スキルが今欲しい操作は、ingest 時に「既存のどの concept 群と同じテーマ塊か」を機械的に取ることである。画面の色と ID を揃えることではない。

## 2. 非目標

- プラグインから vault ノートへ `community:` を書くこと
- 画面のコミュニティ ID / 彩色 / 分割との一致。graphology の Directed Louvain（Dugué–Perez）を再現すること
- `graph.json` へのコミュニティ混入
- 間隙 4 信号の移植（`gap.ts`）
- retrieve の same-chunk 加点
- NetworkX 等の第三ライブラリ
- `wiki-graph.py` の走査関数をそのまま借りること（あちらはリンク先を `set` に落とす。回数重みが消える）
- テーマ塊を `wiki-survey` の精読クラスタの代わりにすること

## 3. 正本

| 問い | 正本 | スキル |
|---|---|---|
| 塊は画面上でどう見えるか | wiki-lens（`metadataCache` + 有向 Louvain） | 見ない。ID を引かない |
| この既存ページはスキル世界でどのテーマ塊か | `wiki-clusters.py` → `.vault-meta/clusters.json` | CLI のみ。JSON を Read しない |
| 構造的間隙の候補は何か | Gap Finder の Copy report | `wiki-gap` 現行。`wiki-clusters.py` は呼ばない |
| 語彙・共起の近傍は何か | `wiki-graph.py` / `retrieve.py` | 現行のまま |

プラグイン報告の `#id` と `clusters.json` の id は別世代である。突き合わせるコードも手順も置かない。

## 4. レシピ（スキル世界。独自正本）

画面の「同じレシピ」ではない。wiki-lens から借りるのは刈り込みの発想（seed entity と孤立を落とす、`surveys/` を入れない）と、表示名の優先順位（concept を先にする）だけである。目的関数・解決器・被リンクの母集団は揃えない。

### 4.1 節

- 対象: `wiki/{sources,entities,concepts,questions}/` の `*.md`（`_` で始まる名前は除く）
- `surveys/` は節にしない（画面も doctor の clusters 鮮度も入れない）
- `type` は frontmatter、無ければディレクトリ
- `type: entity` かつ `status: seed` は落とす
- 辺を張ったあと次数 0 の節は落とす

### 4.2 辺

- 本文と frontmatter の `related` / `sources` にある wikilink
- 解決は **stem のみ**。alias は結ばない。重複 basename は `list_pages` のソート順で先に出たパスを採用する（`wiki-graph.py` の `by_name.setdefault` と同じ）
- 一次レイヤー・添付・`.raw/` へのリンクは捨てる
- 重みは **出現回数**（`findall`。`set` に落とさない）。同一ページへの 3 回リンクは重み 3
- 自己ループは捨てる
- 無向化する。対 `{a,b}` の重みは両方向の出現回数の和

### 4.3 `in_count`（無向次数重み）

JSON / CLI のフィールド名は `in_count` のままにする。意味は向きのある被リンク数ではなく、バックボーンを無向化したあとの次数重み（接続辺の重みの和。自己ループは 4.4 の超節でのみ次数に入る。4.2 の入力辺に自己ループは無い）。seed entity からの被リンクは入らない。画面の `computeClusters`（全索引 `inCount`）とは一致しない。CLI `--help` に「無向次数重みを in_count と呼ぶ」と書く。

### 4.4 Louvain（無向 Blondel。独自正本）

数式・記号対応・除去/挿入/超節化の展開は [`theme-chunks-algorithm.md`](theme-chunks-algorithm.md)。

graphology の Directed Louvain を追わない。標準ライブラリで無向 Blondel を実装する。rng は `.obsidian/plugins/wiki-lens/src/core/rng.ts` の mulberry32 を同じビット演算で移植する。seed は `0x77696b69`。シングルトン隔離はしない。net ΔQ（`insert(C*) − insert(C0)`）≤ 0 ならその節は留まる。

行列は Newman の無向規約に従う。内部辺重みの合計を `W_int` とする。

- `A` は対称。超節の自己ループは `A_ii = 2 W_int`（ページ入力に自己ループは無いので、葉グラフでは対角 0）
- `k_i = Σ_j A_ij`（超節の次数はメンバー次数の和。`2 W_int + W_ext`）
- `2m = Σ_{ij} A_ij`（よって `m` は無向辺重みの合計。階層しても `m` は不変）
実装が持つ集約値（コミュニティ `C`、節 `i` はまだ入っている）:

- `Σ_tot[C] = Σ_{u in C} k_u`
- `Σ_in[C] = Σ_{u,v in C} A_uv`（対角を含む。内部無向辺は 2 W_int）
- `k_{i,in}(C)` は `i` から `C` 内の他節への辺重み合計（`A_ii` は含めない）
- Blondel 論文の「内部リンク重み」は `Σ_in[C] / 2` である。論文の記号を式に代入するときは必ずこの半分を使う

モジュラリティ（resolution = 1）:

`Q = (1/(2m)) Σ_{ij} [A_ij − k_i k_j / (2m)] δ(c_i, c_j)`

節 `i` を所属 `C0` から外すとき:

- `Σ_tot[C0] ← Σ_tot[C0] − k_i`
- `Σ_in[C0] ← Σ_in[C0] − 2 k_{i,in}(C0) − A_ii`

外したあと（`i` はどのコミュニティにも属さない）、コミュニティ `C` へ入れる ΔQ は、`Σ_in_paper = Σ_in[C] / 2` を Blondel ら 2008 に入れたものである。毎回 Q を全計算して差を取ってはならない。

`ΔQ = [ (Σ_in_paper + 2 k_{i,in}(C)) / (2m) − ((Σ_tot[C] + k_i) / (2m))^2 ] − [ Σ_in_paper/(2m) − (Σ_tot[C]/(2m))^2 − (k_i/(2m))^2 ]`

採用しなければ `i` を `C0` へ戻し、`Σ_tot` / `Σ_in` を外す前の値に戻す。隔離したままにしない（シングルトン隔離は禁止）。

手続き:

1. **フェーズ 1（局所移動）**: 葉節（最初の階層ではページ、以降は超節）を path キーの昇順で全件見る。超節の path キーは「メンバー path の最小」。各節について、所属 `C0` を記録し、外してから隣接コミュニティ（`C0` を含む）への insert ΔQ を計算する。最大の先を `C*` とする。同点なら安定キー（最小 path キー）昇順のうえ `floor(rng() * n)` で 1 つ。rng は同点以外に消費しない。**`C* = C0`、または `insert(C*) − insert(C0) ≤ 0` なら `C0` へ戻し、移動 0 と数える。** コミュニティが変わるときだけ移動 1 とする。**1 周で移動が 0 になるまでスイープを繰り返す**。
2. **超節化**: 各コミュニティを 1 超節にする。コミュニティ間の辺重みは足して `A_ab`（`a≠b`）にする。内部辺は `A_aa = 2 W_int` にする。超節次数はメンバー次数の和で、`m` は変えない。
3. **階層**: 超節グラフに対してフェーズ 1 を繰り返す。超節の走査順は最小メンバー path の昇順。超節数が減らない（フェーズ 1 の最初のスイープで移動 0）なら階層を終える。
4. 採用した移動のあと、Q には `insert(C*) − insert(C0)`（net ΔQ）を足す。insert(C*) だけを足してはならない。net ΔQ は ≥ 0 である。減ったら実装バグである。再計算した Q と累積 Q は一致しなければならない。
5. 収束後、葉ページの所属を `(size 降順, メンバー path の最小の昇順)` で並べ、id を `0..k-1` に振り直す。階層の内部番号は外に出さない。

### 4.5 表示名

各塊の `label` は、メンバーを (type 順位 concept→question→entity→source→その他, `in_count` 降順, path 昇順) で並べ、先頭 2 件の title を ` / ` でつなぐ。画面の `computeClusters` と同じ優先順位だが、`in_count` の母集団が違うので結果は一致しない。一致を試験しない。

`top_members` / `members` の既定出力は、この順の先頭である。これは**ハブ一覧**であり、塊の全成員ではない。

### 4.6 バックボーン外

`membership` に載せない。`lookup` は節が存在するときだけ `on_backbone: false` と `reason: seed | isolated` を返す。多数決帰属は置かない。ingest がテーマ塊を引く対象は **既存 concept** に限る。

## 5. 成果物

パス: `.vault-meta/clusters.json`（gitignore 済み。`graph.json` と同じ導出キャッシュ）。

スキルはこのファイルを Read しない。読むのは CLI の stdout だけである。

```json
{
  "version": 1,
  "built_at": "2026-09-11T17:00:00",
  "recipe": "skill-backbone-undirected-blondel",
  "seed": 2003396969,
  "opts": {"include_seed_entities": false, "drop_isolated": true},
  "topology_sha256": "<下記の正規化バイト列の SHA-256 hex>",
  "stats": {
    "nodes": 0,
    "edges": 0,
    "clusters": 0,
    "dropped_seed_entities": 0,
    "dropped_isolated": 0
  },
  "clusters": [
    {
      "id": 0,
      "label": "概念A / 概念B",
      "size": 120,
      "top_members": [
        {"path": "wiki/concepts/概念A.md", "title": "概念A", "in_count": 40, "type": "concept"}
      ]
    }
  ],
  "membership": {
    "wiki/concepts/概念A.md": {
      "id": 0,
      "type": "concept",
      "title": "概念A",
      "in_count": 40
    }
  }
}
```

`topology_sha256` の正規化バイト列（UTF-8、LF）。title / `updated` / `status`（seed 昇格以外）は入れない。seed 昇格は節集合が変わるので自然に入る。

```
v1
<node_count>
<sorted paths, one per line>
<edge_count>
<pathA>\t<pathB>\t<integer_weight>
```

辺行は `pathA < pathB` の対だけを path 対の昇順で出す。

`id` は **この `topology_sha256` の世代にだけ**有効である。wiki ページ・台帳・frontmatter・プラグイン報告に書いてはならない。報告に残してよいのは `built_at` と `topology_sha256` とパスと label である。

書き込みは `wiki-graph.py` の `atomic_write` と同じ（同ディレクトリの一時ファイルへ書き、`fsync`、`Path.replace`）。並行 `build` は last-writer-wins。読者は置換前の旧ファイルを見てよい。直接上書きは禁止。

## 6. CLI

`python3 scripts/wiki-clusters.py <cmd>`

ページ引数の正規化は `wiki-graph.py` の `normalize_page` と同じ（vault 相対パス / stem / `[[wikilink]]`）。

| コマンド | 役割 | 終了コード |
|---|---|---|
| `build` | 走査して原子書き込み | 失敗 1 |
| `list` | 塊を size 降順（id, label, size, top_members）。空グラフは `[]` で終了 0 | 未構築 3 |
| `lookup <page>` | 1 ページの所属 | 未構築 3。正規化しても 4 ディレクトリにファイルが無い → 3。存在して落ちた節 → 0 かつ `on_backbone: false` と `reason`。所属あり → 0 |
| `members <id>` | その塊のハブ一覧。引数は整数 id のみ。既定 `--top 20`。`--type concept` で絞る。`--top 0` はスクリプト内の一括処理専用（スキルは使わない） | 未構築・不明 id は 3 |
| `assign --pages FILE` | 母集団パス（1 行 1 パス）を塊 id に写す。stdout は `{id, label, size_in_population, paths}` の配列と `unassigned`（バックボーン外・未知）。スキルは JSON を Read せずこの出力だけを見る | 未構築 3。FILE 無し 2 |
| `stats` | `stats` と `topology_sha256` | 未構築 3 |

置かないもの: `gaps`、`neighbors`、`export-markdown`、`members <label>`。

## 7. 鮮度と再構築

鮮度入力は `wiki/{sources,entities,concepts,questions}/` だけである。`surveys/` の mtime は見ない。

- `wiki-doctor.py` の `derived_caches` は **キャッシュごとにページ集合を取る**。`ctx["pages"]`（`surveys/` 込み）を clusters に使ってはならない。`graph.json` は現行どおり `wiki_pages()`（5 ディレクトリ）。`clusters.json` は `wiki/{sources,entities,concepts,questions}/` だけ。無ければ WARN（fix: `python3 scripts/wiki-clusters.py build`）。その 4 ディレクトリの mtime が新しければ stale
- 無効化判定と再構築トリガは同じ 4 ディレクトリである。再構築しても `topology_sha256` が同じなら id は変わらない（決定的分割 + 振り直し）
- `wiki-retrieve-refresh.py` は `wiki-graph.py build` のあと `wiki-clusters.py build` を呼ぶ。clusters は `surveys/` を読まない。失敗しても retrieve は落とさない（`clusters_ok`）。数千ページ規模では graph 再構築に加えて数秒〜十数秒を許容する
- ingest は現行どおり refresh を 1 回呼ぶ。clusters 専用の毎ページ再構築は置かない
- 未構築のときだけ、呼ぶスキルは `build` を 1 回走らせてよい。stale の扱いは呼ぶスキルに従う。ingest は §8.3（既存 concept の所属を見るので stale でも建て直さない）。doctor の WARN は人間または refresh が直す。JSON を Read して補完してはならない。`build` しても無ければ「テーマ塊キャッシュ無し」と書いて resolve / retrieve へ落ちる

全消し手順は `clusters.json` も消す。本ハーネスに `wiki-retrieve` skill は無い（上流）。正本は `README.md` と `wiki-retrieve-refresh.py` の docstring である。

## 8. スキル契約（同じ出荷で本文を書く）

段階を「CLI だけ先に出す」と置かない。基盤と、呼ぶスキル 1 本と、呼ばないスキルの封印を同時に出す。

### 8.1 全スキル共通

`templates/wiki-CLAUDE.md`（install 後は `wiki/CLAUDE.md`）に次を入れる。scripts 列挙の 1 句は `lookup` / `members` に限る。`assign` を「survey 用」と書いてはならない。

- テーマ塊 id を wiki ページへ書かない
- Gap Finder 報告の `#id` と `wiki-clusters.py` の id を突合しない
- `.vault-meta/clusters.json` を Read しない。見るなら `wiki-clusters.py lookup` / `members`

禁止の正本は `templates/wiki-CLAUDE.md` と ingest 本文である。`conventions/token-discipline.md`（install 後は `wiki/meta/token-discipline.md`）はエージェントが Read できない（`Read(**/*token*)` hook）。同じ出荷の受け入れに入れない。人間が hook 解除後に禁止列挙へ追記する。CLI が失敗したときの落ち先は resolve / retrieve だけである。「JSON を開け」と書いてはならない。`assign` の `--help` は「wiki-survey は呼ぶな」と出す。

### 8.2 封印（呼んではならない）

`wiki-gap` に必須追記:

> `wiki-clusters.py` を呼ばない。Gap Finder 報告の `#id` を `clusters.json` および `members` と突合しない。間隙の正本は Copy report のままである。

`wiki-survey` に必須追記:

> フェーズ 3 の「クラスタ」は母集団の編集分割（精読クラスタ）である。`wiki-clusters.py` のテーマ塊とは別物である。本 skill はテーマ塊 CLI を呼ばない。

`wiki-query` / `wiki-ideate` / `wiki-thesis` は改稿しない（呼ばない）。本ハーネスに `wiki-retrieve` skill は無い。

### 8.3 呼ぶスキル（この出荷の利用面）

`wiki-ingest` と `wiki-ingest-paper` の concept 手順。`wiki-ingest` / `wiki-ingest-paper` の本文で、この手順の直前に「`.vault-meta/clusters.json` を Read するな」を再掲する。

対象は **今回新規作成または更新する concept ページだけ**である。ハブ側へ相互の related は足さない。entity と source は `lookup` しない。frontmatter `related:` は触らない。

書き先:

- 新規: `wiki-page-write.py` 初回本文の `## 関連` 行 `- 概念: [[...]] / [[...]]`
- 更新: 同じページの `wiki-append.py --batch` の `related.概念`（既存の inbox / 出典と同じ 1 回の batch）。別 Edit を増やさない

上限（整数）:

- 1 ページの `- 概念:` は、既存分を含め最大 5 件
- うちテーマ塊由来は最大 3 件
- この 3 件は、今回の concept 新規 3 / 更新 5 の枠を消費しない（related 追記であり、concept ページの新設・更新カウントではない）

手順（ページ本文を書く**前**。新規ページはまだ membership に無い）:

1. 今回 `wiki-resolve.py --compact`（または JSON）が当てた**既存 concept** を score 降順で最大 5 件取り、`lookup` する。source / entity は使わない
2. キャッシュは **直前の refresh 済み**を信じる。未構築（`lookup` 終了 3）のときだけ `build` を 1 回試す。stale でも ingest 中は建て直さない（見るのは既存 concept の所属。新規ページはまだ無い）。`clusters_ok` が false の refresh のあとはテーマ塊を使わない
3. `on_backbone: true` の id について `members <id> --type concept --top 20` を取る。自分と今回新規名と、すでに `- 概念:` にあるものを除く
4. resolve 集合に無いハブを、`members` の出力順（type 順位、`in_count` 降順、path 昇順）の先頭から最大 3 件取る。4 件目以降は開かない。すでに excerpt 済みのハブは追加読みしない
5. 追加ハブは、更新対象の excerpt と **同じ** `wiki-excerpt.py` 呼び出しに載せる。`--budget-tokens 1800`、`--total-budget` は `(今回更新する concept 数 + 追加ハブ数) * 1800` 以上。スキル本文の固定「9000」は、更新 5 かつ追加ハブ 0 のときの下限であり、追加ハブがあるときは上の式に替える。切れたページは読んだことにせず related に足さない
6. excerpt に、今回 source の主題と接する記述が無いハブは捨てる。接するものだけを `- 概念:` に足す（既存分と合わせて 5 件まで、塊由来は 3 件まで）
7. 未構築のまま / 既存 concept ヒット 0 / 全て `on_backbone: false` / excerpt して接するハブ 0 なら、テーマ塊を使わず今どおり resolve 結果だけを related にする。JSON を開けて補完してはならない

### 8.4 後続（この出荷の対象外）

- `wiki-survey` が `assign --pages` で精読の初期案を出すこと
- `wiki-gap` のスクリプト化
- retrieve 加点

`assign` は CLI として実装する（試験と将来のため）。survey からは呼ばない。

## 9. 棄却した案

- プラグインが `clusters.json` を書く（エージェントは Obsidian を起動しない）
- プラグイン core の Node CLI 化（`GraphIndex` が `metadataCache` 依存）
- `graph.json` を濾して Louvain（辺種が残っていない）
- frontmatter へ community を書く
- 無向 Blondel を「wiki-lens と同じレシピ」と呼ぶ（画面は有向 Louvain、走査は alias 解決、`inCount` は全索引）
- CLI だけ先に出し、skill 本文は「言及してよい」で済ませる（wiki-gap が `#id` を `members` に突き合わせる）
- 間隙まで出して「クラスタ利用」と呼ぶ（正本が二つになる）

## 10. 試験

`scripts/test_wiki_clusters.py`。一時 vault。`WIKI_VAULT_ROOT`。実 wiki のスナップショット一致は試験しない。

必須:

1. 3 クリークを橋 1 本でつないだグラフが 2 塊に分かれ、交差辺はその橋 1 本だけである
2. 各採用移動の net ΔQ（`insert(C*) − insert(C0)`）が ≥ 0 であり、累積 Q が再計算した Q と一致する。`C0` 再加入は移動に数えない
3. 同一ページへのリンク 3 回が重み 3 になる
4. alias だけでは辺が張られない
5. `wiki/surveys/` のページは節に入らない
6. `in_count` はバックボーン辺の次数重みである（seed からの被リンクを足さない）
7. 存在しないページの `lookup` は終了 3
8. 存在する seed entity の `lookup` は終了 0、`reason: seed`
9. title だけ変えても `topology_sha256` は不変
10. 重複 basename はソート順の先勝ち
11. 同じトポロジなら membership の id 割り当てが一致する。超節化が 1 回以上起きるグラフ（大きさ 3 のクリーク 3 つを橋で直列）でも、固定 seed で 2 回の `build` が一致する
12. 表示名は、`in_count` が大きい source より concept を優先する
13. `members` 既定は 20 件
14. `assign --pages` が母集団外を `unassigned` に出す
15. 空グラフの `list` は終了 0 で空配列
16. 未構築の `lookup` は終了 3
17. `wiki-doctor.py --only derived_caches` は、`wiki/surveys/` だけ新しいとき `clusters.json` を stale にしない（`graph.json` は現行どおり stale になりうる）

## 11. 受け入れ（基盤 + 利用 + 封印）

1. `wiki-clusters.py build` が空入力と非空の試験グラフで失敗せず、非空では `list` が 1 塊以上を返す。空入力の試験は §10.15。知識ページの無いハーネス本体では実 wiki のスナップショット一致は見ない
2. doctor がキャッシュごとにページ集合を分け、clusters は 4 ディレクトリだけを見て欠落 / stale を WARN する（§10.17）
3. refresh が `clusters_ok` を返し、`surveys/` を clusters 走査に使わない
4. §10 の試験が通る
5. どの wiki ページの frontmatter も増えない
6. `wiki-ingest` と `wiki-ingest-paper` の concept 手順が §8.3 を本文に持ち、手順直前に JSON Read 禁止を再掲する
7. `wiki-gap` と `wiki-survey` が §8.2 の封印文を本文に持つ
8. `templates/wiki-CLAUDE.md` が §8.1 を持ち、scripts 句は `lookup` / `members` に限る
9. README の全消し手順が `clusters.json` を含む
10. （欠番。`token-discipline.md` 追記は人間作業であり受け入れから外す）

## 12. レビューで捨てた前提

初稿は「wiki-lens と同じレシピを再計算する」と「段階 0 は CLI を露出するだけ」を同時に置いた。画面は有向 Directed Louvain で、`wiki-graph` はリンクを集合化する。同じものにはできない。CLI を先に出すと、既存の `wiki-gap` が報告の `#id` を `members` に突き合わせ、`wiki-survey` が精読クラスタと混同する。

残したのは、スキル世界に独自の無向 Blondel を置き、画面と間隙の正本は触らず、最初の利用者を ingest の `- 概念:` 追記 1 本に限ることである。related は `wiki-append` の `related.概念` / 初回本文に固定し、未読ハブは最大 3 件まで excerpt してからしか足さない。
