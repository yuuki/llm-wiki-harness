# Wiki Lens

LLM wiki レイヤー（`wiki/{sources,entities,concepts,questions}/`）の健全性・骨格・近傍・成長・多層・間隙を見る、読み取り専用の Obsidian プラグインである。

ノートは書かず、通信もしない。グラフは Obsidian の `metadataCache` から組み立てる。ファイルを直接読むのは時間軸の注釈だけで、対象は `wiki/log.md` と月次アーカイブ `wiki/log/YYYY-MM.md` である。vault の一次ノート層は見ない。

英語の原版は [`README.en.md`](README.en.md) にある。設計は [`docs/design.md`](docs/design.md)、実装は [`docs/implementation.md`](docs/implementation.md)、アルゴリズムは [`docs/algorithms.md`](docs/algorithms.md) に書いた。画面のクラスタリング（Louvain）と、skill が使うテーマ塊（無向 Blondel。[`docs/clusters-for-skills.md`](docs/clusters-for-skills.md) / [`docs/theme-chunks-algorithm.md`](docs/theme-chunks-algorithm.md)）は別物である。

## 6 つのビュー

| ビュー | 答える問い | 要点 |
|---|---|---|
| Health Dashboard | 健全か | 件数、死リンクの原因分類、育てる seed、孤立、basename 衝突 |
| Backbone Graph | 全体の形は何か | Louvain クラスタの俯瞰、全ノード、1 クラスタの中 |
| Local Lens | 今読んでいるノートの近傍に何があるか | Personalized PageRank。欠ページは赤い幽霊 |
| Growth Timeline | どう増えたか | `created` で再生。2 時点差分。種別ごとの枚数 |
| 3D Layers | 層はどう結合するか | Z = ページ種別。XY は同じ ForceAtlas2 |
| Gap Finder | 無い接続は何か | 疎なクラスタ対、共引用、予測辺、未取り込み文献 |

リボンとコマンドパレット（`Wiki Lens: Open …`）から開く。

Gap Finder の **Copy report** は構造上の候補だけを出す。意味の判定と橋渡し文献の推薦は、vault 側の `wiki-gap` skill が行う。プラグイン自体は通信しない。

## 標準グラフとの差

- `index.md` / `log.md` / `_index` を次数計算から外す
- 揺れる `domain` タグの代わりに Louvain で塊を取る
- entity stub は全体図では既定で隠す
- ハブ概念では BFS ではなく PPR で近傍を取る
- 「まだ無い辺」は破線で描き、普通の辺にはしない

## vault への展開

ハーネス同梱の `main.js` があれば、ビルドせずに置ける。

```bash
# ハーネスルートから
./install.sh /path/to/your-vault

# プラグインだけ
./plugins/wiki-lens/install-to-vault.sh /path/to/your-vault
```

手で置く場合:

1. このディレクトリを `<vault>/.obsidian/plugins/wiki-lens/` にコピーする（`node_modules` と `data.json` は不要）
2. `<vault>/.obsidian/community-plugins.json` に `"wiki-lens"` を足す
3. Obsidian を再読み込みし、コミュニティプラグインで Wiki Lens をオンにする

動作条件は Obsidian 1.5 以降のデスクトップ版で、WebGL が要る。WebGL が無い環境では、グラフビューの代わりに案内文を出す。

## 開発

```bash
cd plugins/wiki-lens
npm install
npm test
npm run dev      # 監視ビルド
npm run build    # 型検査 + 本番 main.js
```

`main.js` は esbuild でまとめる。実行時の依存は graphology（Louvain / ForceAtlas2）、sigma v3、three.js である。three.js は 3D の描画だけに使い、力計算はしない。

Obsidian が起動しているときの確認:

```bash
obsidian plugin:reload id=wiki-lens
obsidian dev:errors
```
