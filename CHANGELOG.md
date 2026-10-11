# Changelog

このハーネスの**利用者向けの差分**を新しい順に記す。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)。バージョンは [Semantic Versioning](https://semver.org/lang/ja/) に寄せる。

対象は skill・スクリプト・規約・`install.sh`・wiki-lens の挙動である。知識ページ（`wiki/sources` など）は対象外。

次の公開まで、差分は必ず `[Unreleased]` に追記する。タグを切るときに `[Unreleased]` をバージョン節へ移す。

## [Unreleased]

### Added

- `bib-lookup.py`。OpenAlex / Crossref を 1 つの CLI に集約する（`retracted` / `meta` / `pair` / `cites-both` / `refs` / `abstracts` / `population` / `search`）。無料枠の残額をヘッダで見て、尽きたら終了コード 4、通信失敗は 5 で止まる。`bibliography-lookup` の補助経路として書き、`wiki-lint`（撤回照会 check 19 と `## Retractions` 節）・`wiki-gap`（`cites-both` による橋渡し検索）・`wiki-ideate`・`wiki-publish`（書誌の正式化と撤回確認）・`wiki-survey-recommend`（母集団の厚み）・`wiki-ingest-paper`（出版社版の書誌、arXiv 版との対応）から呼ぶ。`test_bib_lookup.py` を足した。
- `fetch-paper-pdf.sh` が ACM DL / IEEE Xplore で curl が弾かれたとき、終了コード 5 と `browser_fetch=` などの引き継ぎ出力を返す。手順は `wiki-ingest-paper/references/publisher-browser-fetch.md`（Claude in Chrome 拡張つきのブラウザで PDF を落とし、ローカルパスで取り込み直す）。PVLDB は `www.vldb.org` の公開 PDF を curl で取る。
- `bm25-index.py` が SQLite 鏡像（`.vault-meta/bm25/index.sqlite`）も書く。`retrieve.py` は鏡像が JSON と同じ版なら語ごとの posting だけを読み、巨大な JSON を全部読み込まない。古ければ JSON に戻る。`bm25-index.py sqlite` で鏡像だけ作り直せる。`wiki-doctor.py` に `bm25_sqlite` 検査を足した。
- `dense-index.py`。ページ先頭（chunk-000）を 1 ページ 1 ベクトルで埋め込み、`.vault-meta/dense/` に置く。`retrieve.py` は語の重なりが無い言い換えの問いでも、この索引から近いページを rerank の候補に足す（ローカル ollama のみ。無ければ従来どおり BM25 とグラフだけ）。`wiki-retrieve-refresh.py` が BM25 の後に差分だけ更新し（`--no-dense` で省略）、`test_dense_index.py` を足した。
- `retrieve.py --also "<言い換え>"`。言い換えごとにパイプラインを走らせ、元の問いに 2 倍の重みを付けて RRF で融合する（候補に `multi_score` / `matched_queries`）。手順は `skills/wiki-query/references/agentic-followup.md`（最初の retrieve が問いの対象を外したときだけ、名前の照合 → 言い換え → 近傍 → `rg` を最大 2 周）。`wiki-query` / `wiki-survey` / `wiki-thesis` から参照する。

### Changed

- `rerank.py` の既定の埋め込みモデルを `qwen3-embedding:0.6b` にし、モデルごとの文書接頭辞と `WIKI_EMBED_MODEL` での差し替えに対応した。
- `claim-audit.py numbers` は先頭の 0 を省いた小数（`.5`）も数値として照合する。
- `wiki-ingest` は記事内の mermaid ブロックも図として source ページへ載せる（原本のコードをそのまま使い、ラベルだけ和訳してよい）。
- `wiki-survey` は書籍ソース（`publish: false`）の図も、選定基準を満たせば確認なしで転載する（source と survey の `publish` は変えない）。
- `retrieve.py` の検索を `Searcher` に切り出し、常駐する呼び出し側がインデックスを再利用できるようにした。`wiki-context-pack.py` の束作りも `pack()` に切り出した（CLI の挙動は変えない）。
- `wiki-refactor` の再編纂手順に、実行環境の注意（シェル差、ロックの二重取り、並行セッションの巻き込み確認、`prepend-log` の訂正）、観察と出典の食い違いを突き合わせる 4 点、ハブ entity にだけ依る観察の張り替え、`claim-audit.py sample` の stderr 分離を足した。
- `wiki-ingest-thesis` は ACM / IEEE のサーベイを取るとき、`publisher-browser-fetch.md` の手順で PDF を手元に落としてから `fetch-book.sh` に渡す。

## [0.6.0] - 2026-10-04

サーベイのテーマ推薦、概念の表記揺れのゲート、再編纂の確定手順、log の月次ローテーションを置き、ingest と再編纂の台帳操作を並行セッションに耐えるようにする。

### Added

- `wiki-survey-recommend`。設問が未定のとき、蓄積から `wiki-survey` のテーマ候補を ready / 姉妹 / 要 ingest / 別 skill に振り分ける。判定の材料は `wiki-survey-recommend.py` が出し、skill はその stdout だけを見る。
- `wiki-survey-check.py`。`wiki/surveys/` 長編ページの検証（wikilink・画像の実在、ダッシュと中黒、LLM 口調、同一出典の連続一文一段落、図番号、sources の同期）を 1 コマンドにし、`stats` で既存ページを実測する。`wiki-survey` に既存ページの増補の流れを足した。
- `concept-resolve.py`。表記の揺れ（方言）で分かれた概念を同じページへ寄せる。統合候補の対の提案、lookup、新設のゲートを持つ。`wiki-resolve.py` が `dialect-gate` を返した候補について、ingest は concept を新設せず台帳に積む。
- `paper-refs.py`。取り込み済み論文の文献表から識別子候補を `.vault-meta/paper-ref-candidates.json` に積む。本文の `[N]` と節見出しから引用の役割を、直前の文脈から対比の言及を数える。候補を自動で ingest はしない。
- `recompile-finalize.sh`。再編纂 1 ページ分の確定を、台帳 → 索引 → パス限定コミットの順で行う。索引の失敗では止まらない。
- `wiki_questions.py`。`## 未解決の問い` に紛れた出典付きの断定を数える。`wiki-append.py` は問い節への出典付きの命題の追記を拒み（紛らわしい項目は WARN）、`wiki-concept-stats.py` は compile 負債に数える。
- `wiki_sections.py`。concept の機能節と主題節の分け方を `wiki-concept-stats.py` / `wiki-excerpt.py --outline` / `wiki_claims.py` で共有する（参照一覧の節と受信箱の案内文を主題・観察に数えない）。
- `wiki-manifest.py`。`.raw/.manifest.json` の原本エントリを `get` / `put` / `normalize` で 1 件ずつ、ロックの内側で読み書きする。ingest skill は manifest を Read も手書きもしない。
- `address-backfill.py`。`address:` を持たないページへの後付け採番。`wiki-doctor.py` が欠落を報告する。
- `crop-paper-figures.py`。論文のベクター図をキャプション基準でクロップする（二段組みで本文を巻き込まない）。
- `video-add-frames.py`。選んだ候補フレームを `.raw/videos/<slug>/frames/` へ続き番号で足す。
- `claim-audit.py numbers`。命題・根拠行・ingest の観察の数値を、source ページと `.raw` の抽出テキストに機械で照合する。`wiki-ingest-paper` / `wiki-ingest-book` / `wiki-ingest-thesis` も、source を書いた直後にこれで照合する。
- `wiki/log.md` の月次ローテーション。`wiki-catalog.py prepend-log` が過去月のエントリを `wiki/log/YYYY-MM.md` へ移す。`wiki-doctor.py` が移し漏れを検査する。
- `wiki-ingest-book` の章ブリーフを役割で分け、読み手用の `references/reader-brief.md` を足した。
- 規約に、数式は Obsidian の `$` / `$$` 記法だけで書くという規則を足した。

### Changed

- concept の新設・保留の判断に研究関心プロファイルを使わない（conventions §12 ルール 3）。判断は resolve の結果と独立 source 数だけで行う。`wiki-profile.py` は query / survey の枠付けとテーマ推薦だけが読む。
- retrieve の対象を知識ページ（`wiki/{sources,entities,concepts,questions,surveys}/`）に絞り、カタログ・`log/`・`meta/` を chunk にしない。`wiki-retrieve-refresh.py --all` は `contextual-prefix.py --prune` で古い chunk を消す。
- BM25 の構築は他の構築が握るロックを最大 180 秒待つ（`BM25_LOCK_WAIT_SEC`）。`recompile-queue.py` と claim-audit の台帳も read-modify-write をロックで囲む。
- `templates/wiki-CLAUDE.md` に並行セッションの規則（全件再構築を避ける、台帳を索引の後ろに `set -e` で繋がない、パス限定コミット）を足した。
- `wiki-refactor` の再編纂と命題再検証の手順を具体化した（統合見出し、出典の無い散文、問い節の断定、矛盾、定義前の段落の扱い。確定は `recompile-finalize.sh`）。
- `wiki-ingest-book` / `wiki-ingest-thesis` を章ごとに並列で動かすとき、concept の新設枠を章の書き手に配らない。新設する concept と担当章はオーケストレータが名指しする。
- `wiki-ask-source` / `wiki-brief-source` は採番に失敗したらノートを保存しない（address カウンタを手で読み書きしない）。
- `wiki-brief-source` の紹介文に論旨の順（課題 → 既存の限界 → 中核アイデア → 仕組み → 検証設定 → 主要結果 → 限界 → 含意）を定め、10 行を基本に 20 行まで伸ばしてよいとした。
- `wiki-publish` は本文の `(Source: [[...]])` を主張と出典の対応の正本とし、書誌は source の frontmatter から集める。
- `wiki-ingest-paper` は原本の Abstract を `> [!abstract] 概要` callout に置く。
- `wiki-ingest-video` で、動画の取得を失敗に強くし、スライドを映したフレームの選び方を改めた（`prepare-video-source.sh`）。
- wiki-lens 0.2.0。Growth Timeline が `wiki/log.md` に加えて `wiki/log/YYYY-MM.md` の月次アーカイブも読む。

### Removed

- `wiki_tokenize.py`。和文 BM25 の分かちは同じ規則の `wiki_cjk.py` に移した（索引の作り直しは不要）。
- `bin/s2.sh`。どの skill も呼んでいなかった Semantic Scholar Graph API クライアント。外側照合は `skills/bibliography-lookup.md` の識別子優先の経路で行う。

## [0.5.0] - 2026-09-14

出典付きのグラフ路と開いているページの配役近傍を置き、和文 BM25 を CJK 2-gram にする。

### Added

- ページグラフ version 2。主題節の命題から出典付き辺（`claim-link` / `claim-cite`）を取り、`retrieve.py` のグラフ路候補に `via` を付ける。抽出は `wiki_claims.py`。
- `wiki-related`。開いている 1 ページの隣を contradiction / evidence / definition / cluster の役で出す。キャッシュは `.vault-meta/related/`。スキルは stdout だけ見る。
- 和文 BM25 の CJK 2-gram 分かち（`wiki_tokenize.py`）。索引 schema 4。古い分かちは `wiki-doctor.py` が WARN する。
- `usage-report.py` が Cursor と Codex のセッションも集計する。

### Changed

- `AGENTS.md` に、正本は研究 vault・更新は研究 vault → 本リポジトリ・リリースの切り方・一般化・テーマ塊の分業を書いた。
- `claim-audit.py` の命題抽出を `wiki_claims.py` に寄せる。
- `contradiction-index.py` の `collect` / `list_pages` / `extract_callouts` が `vault=` を受ける（wiki-related が種の vault で読む）。
- graph / clusters を建て直したら `.vault-meta/related/` を消す（`wiki-graph.py` / `wiki-clusters.py` / `wiki-retrieve-refresh.py`）。
- `wiki-query` は種がページパスのとき `wiki-related` へ渡す。

## [0.4.0] - 2026-09-13

スキル世界のテーマ塊を置き、ingest が concept の関連リンクを機械的に足せるようにする。

### Added

- スキル世界のテーマ塊。`wiki-clusters.py`（無向 Blondel）が `.vault-meta/clusters.json` を書き、ingest が concept の `- 概念:` を足すときに `lookup` / `members` だけを呼ぶ。契約は `plugins/wiki-lens/docs/clusters-for-skills.md`、数式は `plugins/wiki-lens/docs/theme-chunks-algorithm.md`。
- `wiki-doctor.py` が `clusters.json` をキャッシュごとに鮮度検査する（`surveys/` は見ない。欠落は WARN）。
- `wiki-retrieve-refresh.py` が graph のあと `wiki-clusters.py build` を呼ぶ（`clusters_ok`。失敗しても retrieve は落とさない）。

### Changed

- `wiki-ingest` / `wiki-ingest-paper` がテーマ塊から `- 概念:` を提案する。手順の正本は paper の `references/subagent-brief.md` §テーマ塊。
- `wiki-gap` と `wiki-survey` フェーズ 3 はテーマ塊 CLI を呼ばない（封印）。
- `wiki-lint` の doctor fix に `wiki-clusters.py build` を足す。
- `templates/wiki-CLAUDE.md` にテーマ塊の鉄則（id 非書込み、`#id` 非突合、JSON 非 Read、`lookup` / `members` のみ）。

画面の Directed Louvain とスキル世界の無向 Blondel は揃えない。`assign` は試験と将来フックであり、survey は呼ばない。`token-discipline.md` は `Read(**/*token*)` hook のため同じ出荷では触らない。

## [0.3.0] - 2026-09-11

既読 source の軽い質問と配布用紹介文を、知識とは別の派生ノートとして残す。

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
