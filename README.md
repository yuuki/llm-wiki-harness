# llm-wiki-harness

研究用 Obsidian vault で育てた **LLM wiki の再利用可能なハーネス**である。知識そのもの（論文要約、概念本文、教科書）は含まない。入れるのは、取り込み・照会・編纂・点検の手順と、それを支えるスクリプト、規約、可視化プラグインである。

チャットは操作面、成果物は vault 内の `wiki/` ページである。

## 入れるもの / 入れないもの

入れる:

- 媒体別 ingest（論文・書籍・博士論文・スライド・動画）と、vault 側で分岐した `wiki-ingest` / `wiki-query` / `wiki-lint`
- `wiki-survey` / `wiki-publish` / `wiki-gap` / `wiki-refactor` / `wiki-thesis` / `wiki-ideate`
- 日本語整文（`japanese-tech-writing` と `conventions/japanese-style.md`）
- トークン規律用スクリプト（`wiki-resolve.py` / `wiki-excerpt.py` / `wiki-catalog.py` ほか）と、編纂・検索の補助（`contradiction-index.py` / `recompile-queue.py` / `claim-audit.py` / `wiki-graph.py` / `concept-candidates.py` / `paper-ids.py` / `entity-resolve.py` / `wiki-profile.py` / `wiki-context-pack.py` / `wiki-doctor.py`）
- 原本取得（`fetch-paper-pdf.sh` / `fetch-book.sh` / `fetch-slide-deck.sh`）
- 読み取り専用の Obsidian プラグイン `wiki-lens`

入れない:

- `wiki/sources` や `wiki/concepts` の本文、`.raw/` の PDF、lint / gap の日付付きレポート
- 既存ノート層向け skill（`create-paper-note` / `create-conference-note` / `create-tech-note`）
- 上流 claude-obsidian の足場そのもの（`wiki` スキャフォールド、`wiki-fold`、`wiki-cli`、`wiki-mode`、`autoresearch`、`save`、`canvas`）

上流は [claude-obsidian](https://github.com/agricidaniel/claude-obsidian) を vault に入れる。本リポジトリはその上に重ねる特化層である。分岐ファイルの由来は [`NOTICE.md`](NOTICE.md)。利用者向けの差分は [`CHANGELOG.md`](CHANGELOG.md)。

## 前提

- Obsidian 1.5 以降（`wiki-lens` はデスクトップ専用、WebGL が要る）
- Python 3、`pdftotext`（poppler）、論文画像抽出には Node（pdf.js）
- 並行書き込みには `flock`（macOS では util-linux の keg-only で入ることが多い）
- エージェント runtime は Claude Code / Codex / Cursor など、Agent Skills を読めるもの

## vault への展開

```bash
./install.sh /path/to/your-vault
```

`install.sh` は次をコピーする。既存の `wiki/CLAUDE.md` とカタログ類は上書きしない。規約 3 枚（`conventions.md` / `japanese-style.md` / `token-discipline.md`）は `wiki/meta/` へ上書きコピーする。

| このリポジトリ | vault での位置 |
|---|---|
| `skills/` | `.claude/skills/`（`wiki-refactor` は `.agents/skills/` にも置く） |
| `scripts/` | `scripts/` |
| `bin/` | `bin/` |
| `conventions/` | `wiki/meta/` |
| `templates/wiki-CLAUDE.md` | `wiki/CLAUDE.md`（無いときだけ） |
| `plugins/wiki-lens/` | `.obsidian/plugins/wiki-lens/`（`community-plugins.json` にも追記） |

手で置く場合も対応は同じである。skill 内の相対パスは、**vault ルートを作業ディレクトリにしたとき**に `wiki/meta/conventions.md` へ届く前提で書いてある。

## 操作の見取り図

```
.raw/（不変原本）
   │  wiki-ingest-*
   ▼
wiki/sources ──► wiki/entities
       │
       └──► wiki/concepts（受信箱へ積む）
              │ wiki-refactor で主題節へ畳む
              ▼
         wiki-query / wiki-thesis / wiki-survey
              │
              ├── wiki/questions（`type: question` / `type: thesis`）
              ├── wiki/surveys
              ├── wiki-ideate → research/ideas/（無いレイヤーなら承認後も /tmp）
              └── wiki-publish → notes/（任意）

見る: wiki-lens（書かない）
判定: wiki-gap（lens の構造候補を、知識で振り分ける）→ 薄い実在ギャップは wiki-ideate
衛生: wiki-doctor のあと wiki-lint
```

入口は媒体、出口は設問の形で選ぶ。行数や文献本数では決めない。

| 入力 | skill |
|---|---|
| arXiv / 論文 PDF | `wiki-ingest-paper` |
| 書籍 | `wiki-ingest-book`（1 章 = source 1） |
| 博士・長編サーベイ | `wiki-ingest-thesis` |
| スライド | `wiki-ingest-slides` |
| 一般 URL | `wiki-ingest` |

| 設問 | skill |
|---|---|
| 単発・1 ソース・2 対象の差分 | `wiki-query` |
| 1 つの命題の判定（支持 / 反対 / 機序） | `wiki-thesis`（`wiki/questions/`、`type: thesis`） |
| 設計空間・文献地図・対象の像 | `wiki-survey` |
| ギャップや閉じなかった命題からの着想 1 本 | `wiki-ideate`（承認後 `research/ideas/`。無いレイヤーなら `/tmp`） |
| wiki 外の読者へ | `wiki-publish` |

## 規約

正本は 3 枚である。エージェントはカタログ（`index.md` / `log.md` / `hot.md`）を全文読まない。

- [`conventions/conventions.md`](conventions/conventions.md) — ページ形式、出典、concept の受信箱と再編纂
- [`conventions/japanese-style.md`](conventions/japanese-style.md) — 常体、カタカナ / 漢語 / 原語の寄せ方
- [`conventions/token-discipline.md`](conventions/token-discipline.md) — resolve / excerpt / catalog / retrieve / context-pack

関心の軸は任意の `profile.md`（`WIKI_PROFILE_PATH`、または `research/curation/profile.md` / `wiki/meta/profile.md`）を `wiki-profile.py` が要約する。無い vault では終了 3 になり、ingest / query は関心判定を飛ばす。

既存ノートと共存させるときの原則（一次ノートを書き換えない、source に `@` を付ける）も conventions に残してある。別レイヤーが無い vault では、その節を読み飛ばしてよい。

## wiki-lens（Obsidian プラグイン）

`plugins/wiki-lens` は配布可能な Obsidian プラグインである。ノートは書かない。通信しない。見る対象は `wiki/` だけ。`install.sh` は `.obsidian` の有無にかかわらず、`<vault>/.obsidian/plugins/wiki-lens` へ置き、`community-plugins.json` に `wiki-lens` を足す。

プラグインだけ欲しいとき:

```bash
./plugins/wiki-lens/install-to-vault.sh /path/to/your-vault
```

そのあと Obsidian を再読み込みし、コミュニティプラグインで Wiki Lens をオンにする。

| ビュー | 問い |
|---|---|
| Health Dashboard | 健全か |
| Backbone Graph | 形は何か |
| Local Lens | 今のノートの近傍か |
| Growth Timeline | どう増えたか |
| 3D Layers | 層はどう結合するか |
| Gap Finder | 無い接続は何か |

構造の検出までがプラグイン、意味の判定と橋渡し文献は `wiki-gap` である。案内は [`plugins/wiki-lens/README.md`](plugins/wiki-lens/README.md)。算法は [`plugins/wiki-lens/docs/algorithms.md`](plugins/wiki-lens/docs/algorithms.md)。

ビルド済みの `main.js` を同梱している。ソースから作り直すなら:

```bash
cd plugins/wiki-lens
npm install
npm test
npm run build
```

## スクリプトの試験

vault に入れたあと、vault ルートで:

```bash
python3 scripts/test_wiki_token_scripts.py
python3 scripts/test_wiki_write_helpers.py
python3 scripts/test_allocate_address.py
python3 scripts/test_contradiction_index.py
python3 scripts/test_recompile_queue.py
python3 scripts/test_claim_audit.py
python3 scripts/test_wiki_graph.py
python3 scripts/test_concept_candidates.py
python3 scripts/test_paper_ids.py
python3 scripts/test_entity_resolve.py
python3 scripts/test_wiki_profile.py
python3 scripts/test_wiki_doctor.py
```

`wiki-lens` は `cd plugins/wiki-lens && npm test`。

## 変更履歴

利用者向けの差分は [`CHANGELOG.md`](CHANGELOG.md) の `[Unreleased]` に追記する。タグを切るときにバージョン節へ移す。

## ライセンス

MIT。`LICENSE` を見ること。上流由来は `NOTICE.md`。
