# Changelog

このハーネスの**利用者向けの差分**を新しい順に記す。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)。バージョンは [Semantic Versioning](https://semver.org/lang/ja/) に寄せる。

対象は skill・スクリプト・規約・`install.sh`・wiki-lens の挙動である。知識ページ（`wiki/sources` など）は対象外。

次の公開まで、差分は必ず `[Unreleased]` に追記する。タグを切るときに `[Unreleased]` をバージョン節へ移す。

## [Unreleased]

### Added

- `wiki-ask-source`。既読 1 source の軽い確認を `wiki/asks/` に 1 source = 1 ノートで積む（conventions §14、`type: ask`）。
- `wiki-brief-source`。既読 1 source の配布用紹介文を `wiki/briefs/` に残す（conventions §15、`type: brief`）。旧名 `summarize-paper-note`。
- `install.sh` が `wiki/asks/` と `wiki/briefs/` を作る。

### Changed

- 派生ノート（ask / brief）は知識索引に入れない。`contextual-prefix` / retrieve refresh / wiki-graph / tiling / lint の対象外。`wiki-doctor` の address 重複検査だけ含める。
- `wiki-page-write.py` は上書き時に既存 `address` を再利用する（再採番しない）。
- `wiki-verify-ingest.py` は `asks:` / `brief:` とパス修飾リンクを、同じ basename の派生ノートへ誤解決しない。
- `wiki-query` は単一 source の配布紹介を `wiki-brief-source` へ渡し、`wiki/questions/` に保存しない。

## [0.2.0] - 2026-09-11

研究 vault で育てた編纂・検索・判定の一式をハーネスへ移植した。

### Added

- `wiki-thesis`（`type: thesis`）と `wiki-ideate`（承認後 `research/ideas/`。無いレイヤーなら `/tmp`）。
- 編纂補助: `contradiction-index.py` / `recompile-queue.py` / `claim-audit.py`。
- 検索の第 3 路: `wiki-graph.py`（`retrieve.py`。refresh が `graph.json` を再構築）。
- 台帳: `concept-candidates.py` / `paper-ids.py` / `entity-resolve.py`。
- `wiki-profile.py`（`WIKI_PROFILE_PATH`、なければ `research/curation/profile.md` / `wiki/meta/profile.md`。無ければ終了 3）。
- `wiki-context-pack.py` と `wiki-doctor.py`（lint の最初）。
- 外側照合の共有手順 `bibliography-lookup.md`（Semantic Scholar Graph API は使わない）。

### Changed

- `wiki-query` の standard を save-first にした。
- `wiki-gap` の照合を arXiv / DBLP / Crossref に寄せ、S2 クライアントを経路から外した。
- `fetch-paper-pdf.sh` が既存 source を `paper-ids.py` で照合する。`wiki-verify-ingest.py` が `arxiv_id` / `doi` を検査する。
- conventions に `type: thesis`、`arxiv_id` / `doi`、再編纂キュー、concept 候補台帳、entity alias 規則を足した。

## [0.1.0] - 2026-09-09

再利用ハーネスの初版。知識ページは含めない。

### Added

- 媒体別 ingest（paper / book / thesis / slides / video）と `wiki-query` / `wiki-survey` / `wiki-publish` / `wiki-gap` / `wiki-lint` / `wiki-refactor`。
- `install.sh`（skills / scripts / conventions / wiki-lens）。
- トークン規律スクリプト（resolve / excerpt / catalog / retrieve）と原本取得ヘルパー。
- 読み取り専用プラグイン `wiki-lens`。
