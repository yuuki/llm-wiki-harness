# AGENTS.md

このリポジトリは LLM wiki の**ハーネス**である。知識ページ（論文要約・概念本文）は含まない。入れないものの一覧は [`README.md`](README.md)。

応答は日本語でよい。規約の常体・用語寄せは [`conventions/japanese-style.md`](conventions/japanese-style.md) に従う。`cursor/` で始まる git ブランチは作らない。

## 正本

| 対象 | 正本 |
|---|---|
| 製品の見取り図・展開 | [`README.md`](README.md) |
| vault へ入れたあとの運用 | コピー先の `wiki/CLAUDE.md` と `wiki/meta/conventions.md` |
| その雛形 | [`templates/wiki-CLAUDE.md`](templates/wiki-CLAUDE.md) と [`conventions/`](conventions/) |
| テーマ塊の契約 | [`plugins/wiki-lens/docs/clusters-for-skills.md`](plugins/wiki-lens/docs/clusters-for-skills.md) |
| テーマ塊の数式 | [`plugins/wiki-lens/docs/theme-chunks-algorithm.md`](plugins/wiki-lens/docs/theme-chunks-algorithm.md) |

`install.sh` は既存の `wiki/CLAUDE.md` とカタログを上書きしない。規約 3 枚は `wiki/meta/` へ上書きコピーする。skill の正本は `skills/` である。`.agents/skills/` に置くのは `wiki-refactor` だけ。本ハーネスに `wiki-retrieve` skill は無い（上流）。

## 差分とリリース

skill・スクリプト・規約・`install.sh`・wiki-lens の利用者向け差分は、実装と同時に [`CHANGELOG.md`](CHANGELOG.md) の `[Unreleased]` へ追記する。知識ページは対象外。

タグを切るときだけ `[Unreleased]` をバージョン節へ移し、空の `[Unreleased]` を残す。コミットメッセージは `Release X.Y.Z.`、注釈付きタグは `vX.Y.Z`。wiki-lens の `manifest.json` はプラグイン本体の挙動が変わったときだけ上げる。

## 研究 vault から移植するとき

正本は研究 vault である。本リポジトリはその一般化先である。更新の向きは研究 vault → 本リポジトリであり、逆ではない。`install.sh` は**別 vault** へ重ねる展開であり、研究 vault へハーネスを戻す経路ではない。

一般化する。研究 vault の絶対パス、ページ数、所要秒数の実測は書かない。鮮度の目安は「数千ページ規模では数秒〜十数秒」。文書パスはハーネス相対（`plugins/wiki-lens/docs/`）。vault へ入れたあとは `.obsidian/plugins/wiki-lens/docs/`。

新しい `scripts/*` は `install.sh` がディレクトリごとコピーする。既存の `wiki-*.py` は標準ライブラリだけにする。NetworkX は足さない。

`conventions/token-discipline.md`（install 後は `wiki/meta/token-discipline.md`）は `Read(**/*token*)` hook で読めない。同じ出荷の受け入れに入れない。禁止は `templates/wiki-CLAUDE.md` と skill 本文に書く。

## テーマ塊

画面の Directed Louvain とスキル世界の無向 Blondel は別物である。本機能を「wiki-lens クラスタリングの利用」とは呼ばない。分割が食い違っても欠陥ではない。

- スキルは `.vault-meta/clusters.json` を Read しない。見るなら `wiki-clusters.py lookup` / `members`
- 呼ぶのは `wiki-ingest` と `wiki-ingest-paper` だけ（concept の `- 概念:`）
- `wiki-gap` / `wiki-survey` は呼ばない。Gap Finder 報告の `#id` と突合しない
- `assign` は試験と将来フックである。survey は呼ぶな
- テーマ塊 id と `community:` を wiki ページへ書かない
