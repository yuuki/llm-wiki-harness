# テーマ塊アルゴリズム

契約（節・辺・CLI・スキル）の正本は [`clusters-for-skills.md`](clusters-for-skills.md) である。本書はハーネスの `scripts/wiki-clusters.py` が実装する**無向 Blondel**の数式・記号・手続きである。画面の Directed Louvain（`src/core/cluster.ts`）とは別物である。vault へ入れたあとのパスは `.obsidian/plugins/wiki-lens/docs/` である。

参照: Blondel, Guillaume, Lambiotte, Lefebvre, *Fast unfolding of communities in large networks*, J. Stat. Mech. 2008 (P10008)。モジュラリティは Newman 2004 の無向規約。

## 1. 全体

入力はページ間の無向整数重みグラフ $G=(V,E,w)$ である。出力は葉ページの所属 $c:V\to\{0,\dots,k-1\}$ である。

1. vault を走査して有向出現回数を数え、seed / 孤立を落として無向化する（§2）
2. 各節を単独コミュニティとしてフェーズ 1（局所移動）を収束させる（§6）
3. コミュニティを超節に潰し、辺を集約する（§8）
4. 超節数が減るあいだ 2–3 を繰り返す（§9）
5. 葉の所属を size と path で振り直す（§10）

解像度パラメータは常に 1 である。シングルトン隔離はしない。第三ライブラリは使わない。

## 2. 入力グラフ

関数: `scan_vault` → `build_backbone`。

### 2.1 節

対象は `wiki/{sources,entities,concepts,questions}/` の `*.md`（`_` 始まりは除く）。`surveys/` は入れない。`type` は frontmatter、無ければディレクトリ。`type: entity` かつ `status: seed` は落とす。

重複 basename は `list_pages` の順（ディレクトリ順 × 名前昇順）で先に出たパスが勝つ（`by_name.setdefault`）。

### 2.2 有向出現回数

本文と frontmatter の `related` / `sources` から wikilink を `findall` する。解決は **stem のみ**（alias は結ばない）。自己ループと、一次レイヤー・添付へのリンクは捨てる。同一対への 3 回リンクは重み 3 である。`set` に落としてはならない。

### 2.3 無向化と次数

対 $\{a,b\}$（$a<b$）の重みは両方向の出現回数の和である。次数 0 の節は落とす。残った辺の次数重みを `in_count` と呼ぶ（向きのある被リンク数ではない。seed からの被リンクは入らない）。

葉グラフに自己ループは無い。自己ループ $A_{ii}$ は超節化のあとだけ現れる。

`topology_sha256` は節 path と無向辺の整数重みだけを正規化する。title は入れない。

## 3. Newman 行列

無向重みグラフを対称行列 $A$ で表す。内部無向辺の重み合計を $W_{\mathrm{int}}$、コミュニティ外への重み合計を $W_{\mathrm{ext}}$ とする。

| 量 | 定義 | 葉 | 超節 |
|---|---|---|---|
| $A_{ij}$（$i\neq j$） | 無向辺重み | 出現回数の和 | コミュニティ間の辺重みの和 |
| $A_{ii}$ | $2 W_{\mathrm{int}}$ | 0 | 内部辺を対角へ畳んだ値 |
| $k_i$ | $\sum_j A_{ij}$ | 無向次数重み | $2 W_{\mathrm{int}}+W_{\mathrm{ext}}$（メンバー次数の和） |
| $2m$ | $\sum_{ij} A_{ij}=\sum_i k_i$ | $2\sum_e w_e$ | 不変 |

実装の `LevelGraph` は対角を `loops[i]`（$=A_{ii}$）、非対角を `adj[i][j]` に分ける。

```
degree(i) = sum(adj[i].values()) + loops[i]
two_m()   = sum(degree(i) for i in nodes)
```

`two_m` は Blondel / Newman の $2m$ である。階層しても変えない。

## 4. モジュラリティ

$$
Q=\frac{1}{2m}\sum_{ij}\left[A_{ij}-\frac{k_i k_j}{2m}\right]\delta(c_i,c_j)
$$

コミュニティ $C$ ごとの寄与に書き直す。

$$
Q=\sum_C\left[\frac{\Sigma_{\mathrm{in}}[C]}{2m}-\left(\frac{\Sigma_{\mathrm{tot}}[C]}{2m}\right)^2\right]
$$

実装が持つ集約（節 $i$ がまだ $C$ に入っているとき）:

| 記号 | 定義 | コード |
|---|---|---|
| $\Sigma_{\mathrm{tot}}[C]$ | $\sum_{u\in C} k_u$ | `sigma_tot[C]` |
| $\Sigma_{\mathrm{in}}[C]$ | $\sum_{u,v\in C} A_{uv}$（対角を含む） | `sigma_in[C]` |
| $k_{i,\mathrm{in}}(C)$ | $i$ から $C$ 内の**他節**への辺重み（$A_{ii}$ は含めない） | `k_i_in(graph, i, members)` |

内部無向辺は $\Sigma_{\mathrm{in}}$ に $2 W_{\mathrm{int}}$ として入る。Blondel 2008 の「コミュニティ内部のリンク重み」は $W_{\mathrm{int}}=\Sigma_{\mathrm{in}}/2$ である。論文の記号を式に代入するときはこの半分を使う。

`recompute_q` は上のコミュニティ和を毎回作り直す。採用移動のあと累積 $Q$ と一致しなければならない。差を取るために毎回全計算してはならない（契約）。試験だけが `recompute_q` で照合する。

## 5. insert ΔQ

節 $i$ をどのコミュニティにも属さない状態から、コミュニティ $C$ へ入れる増分である。Blondel 2008 の式に $\Sigma_{\mathrm{in,paper}}=\Sigma_{\mathrm{in}}/2$ を入れたもの。

$$
\begin{aligned}
\Delta Q_{\mathrm{insert}}(C)
&=\left[\frac{\Sigma_{\mathrm{in,paper}}+2k_{i,\mathrm{in}}(C)}{2m}-\left(\frac{\Sigma_{\mathrm{tot}}[C]+k_i}{2m}\right)^2\right]\\
&\quad-\left[\frac{\Sigma_{\mathrm{in,paper}}}{2m}-\left(\frac{\Sigma_{\mathrm{tot}}[C]}{2m}\right)^2-\left(\frac{k_i}{2m}\right)^2\right]
\end{aligned}
$$

コード (`insert_delta_q`):

```
sin_p = sigma_in / 2
ΔQ = (sin_p + 2 k_i_in)/two_m - ((sigma_tot + k_i)/two_m)^2
   - (sin_p/two_m - (sigma_tot/two_m)^2 - (k_i/two_m)^2)
```

$A_{ii}$ は式の差で打ち消る。葉でも超節でも同じ ΔQ 式でよい。入れるときの集約更新だけが $A_{ii}$ を足す（§7）。

代数的に整理すると（$A_{ii}=0$ でも一般でも）:

$$
\Delta Q_{\mathrm{insert}}(C)=\frac{k_{i,\mathrm{in}}(C)}{m}-\frac{\Sigma_{\mathrm{tot}}[C]\,k_i}{2m^{2}}
$$

空の $C$（除去直後の単独復帰）では $k_{i,\mathrm{in}}=0$、$\Sigma_{\mathrm{tot}}=0$ なので $\Delta Q=0$ である。

## 6. フェーズ 1（局所移動）

関数: `phase1`。

初期状態は各節が自分自身のコミュニティである。走査順は `path_key` の昇順。葉の `path_key` は vault 相対 path。超節の `path_key` はメンバー path の最小（§8）。

1 スイープは全節をこの順で一度ずつ見る。**移動 0 のスイープが出るまで**繰り返す。

各節 $i$ について:

1. 所属 $C_0$ を記録する
2. $i$ を外す（§7）。この瞬間 $i$ はどこにも属さない
3. 隣接コミュニティ（`adj[i]` の先の所属）に $C_0$ を足した集合へ、それぞれ $\Delta Q_{\mathrm{insert}}$ を計算する
4. 最大の先を $C^*$ とする。同点なら安定キー（そのコミュニティの最小 `path_key`）昇順に並べ、`floor(rng() * n)` で 1 つ選ぶ。rng は同点以外に消費しない
5. $\mathrm{net}=\Delta Q_{\mathrm{insert}}(C^*)-\Delta Q_{\mathrm{insert}}(C_0)$
6. $C^*=C_0$、または $\mathrm{net}\le 0$ なら $C_0$ へ戻し、移動 0 と数える（隔離禁止）
7. コミュニティが変わるときだけ移動 1 とし、$Q\leftarrow Q+\mathrm{net}$ する。$\Delta Q_{\mathrm{insert}}(C^*)$ だけを足してはならない
8. $i$ を $C^*$ へ入れる（§7）

$\mathrm{net}\ge 0$ である。減ったら実装バグである。

隣接に無いコミュニティへは移さない。$C_0$ は隣接に無くても候補に残す（除去で空になっても戻れるようにする）。

## 7. 除去と挿入の集約

$i$ がまだ $C$ に入っているときの $k_{i,\mathrm{in}}(C)$ を $k_{i,\mathrm{in}}$ と書く。

**除去**（$i$ を $C_0$ から外す）:

$$
\begin{aligned}
\Sigma_{\mathrm{tot}}[C_0]&\leftarrow\Sigma_{\mathrm{tot}}[C_0]-k_i\\
\Sigma_{\mathrm{in}}[C_0]&\leftarrow\Sigma_{\mathrm{in}}[C_0]-2k_{i,\mathrm{in}}(C_0)-A_{ii}
\end{aligned}
$$

**挿入**（$i$ を $C^*$ へ入れた**あと**に $k_{i,\mathrm{in}}(C^*)$ を測る）:

$$
\begin{aligned}
\Sigma_{\mathrm{tot}}[C^*]&\leftarrow\Sigma_{\mathrm{tot}}[C^*]+k_i\\
\Sigma_{\mathrm{in}}[C^*]&\leftarrow\Sigma_{\mathrm{in}}[C^*]+2k_{i,\mathrm{in}}(C^*)+A_{ii}
\end{aligned}
$$

$C^*=C_0$ の再加入では、除去前の $\Sigma_{\mathrm{tot}}$ / $\Sigma_{\mathrm{in}}$ に戻る。空になった $C_0$ は、実際に別コミュニティへ移ったときだけ削除する。

## 8. 超節化

関数: `aggregate`。

各コミュニティを 1 超節にする。超節 id はメンバーの最小 `path_key` である。超節の走査順もこの id の昇順になる。

- コミュニティ間 $a\neq b$: 両コミュニティをまたぐ辺重みを足して $A_{ab}$ にする。実装は **超節 $a$ のメンバー側だけ**を辿るので、無向辺は 1 回だけ足る。反対側 $A_{ba}$ は $b$ を処理するときに同じ合計が入る
- 内部: $W_{\mathrm{int}}$ は（旧超節の自己ループの半分）+（メンバー間の無向辺）。`loops[sid]=2 W_int`
- 葉への写像 `leaves[sid]` はメンバーの葉 path を連結してソートする

次数はメンバー次数の和である。$m$ は変わらない。検算: $k_{\mathrm{sid}}=2 W_{\mathrm{int}}+W_{\mathrm{ext}}$。

## 9. 階層

関数: `run_louvain`。

フェーズ 1 のあと、コミュニティ数（空でない `members`）が節数より減り、かつその階層で 1 回以上移動していれば超節化して繰り返す。超節数が減らない、またはフェーズ 1 の移動が 0 なら終わる。

rng は階層をまたいで 1 本の mulberry32 である。各階層で種を戻さない。

葉の所属は、その階層の節→コミュニティを `leaves` 経由で上書きする。外に出す番号は振り直し後だけである。

## 10. 振り直しと表示名

関数: `relabel`、`cluster_label`、`sort_members`。

葉のコミュニティを `(size 降順, メンバー path の最小の昇順)` で並べ、id を $0..k-1$ にする。階層の内部番号は出さない。同じ `topology_sha256` なら id は安定する。

`label` と `members` の順は `(type 順位, in_count 降順, path 昇順)`。type 順位は concept → question → entity → source → その他。先頭 2 title を ` / ` でつなぐ。既定 `members --top 20` はハブ一覧であり、全成員ではない。

## 11. 乱数

`.obsidian/plugins/wiki-lens/src/core/rng.ts` の mulberry32 を同じビット演算で移植する（`>>>` は論理右シフト、`Math.imul` は 32 bit 乗算、`| 0` は符号付き 32 bit）。種は `0x77696b69`（ASCII `wiki`）。

同点集合を安定キー昇順にした配列 `tied` に対し、`tied[floor(rng() * n)]` を選ぶ。実装は `int(rng() * n) % n` である。`rng()` は $[0,1)$ なので `% n` は防御である。

## 12. 関数対応

| 関数 | 役割 |
|---|---|
| `scan_vault` | 4 ディレクトリ走査、stem 解決、有向出現回数 |
| `build_backbone` | seed / 孤立を落とす、無向化、`in_count` |
| `leaf_graph` | 葉の $A$（対角 0） |
| `insert_delta_q` | §5 |
| `k_i_in` | $k_{i,\mathrm{in}}(C)$（自己は除く） |
| `recompute_q` | §4 の全計算（試験用） |
| `phase1` | §6–7 |
| `aggregate` | §8 |
| `run_louvain` | §9 |
| `relabel` | §10 の id |
| `assemble` | JSON（`membership` / `clusters` / `dropped`） |

## 13. やらないこと

- graphology の Directed Louvain（Dugué–Perez）を追うこと
- `wiki-graph.py` の走査を借りること（あちらはリンクを `set` 化し、共起辺を足し、`surveys/` を含む）
- 間隙 4 信号、retrieve 加点、frontmatter への `community:` 書き込み
- NetworkX 等の第三ライブラリ
- 解像度 $\gamma\neq 1$、シングルトン隔離

画面の彩色・id と一致しなくてよい。一致を試験しない。
