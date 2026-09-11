---
name: wiki-ingest-thesis
description: "Thesis/survey-specialized wiki ingest for multi-chapter scholarly documents. Use when the source is a PhD/Master's thesis (dissertation) or a LONG survey/SoK paper (roughly 30+ pages with chapter-like section structure): a university repository PDF, an arXiv survey, a ProQuest dissertation, or a local thesis PDF. Stores the immutable original under `.raw/theses/<slug>/`, splits it by chapter, and files ONE source page PER CHAPTER into `wiki/sources/` plus ONE hub entity page (entity_type: thesis|survey) into `wiki/entities/`. Use this — not wiki-ingest-paper (ordinary papers, short surveys) nor wiki-ingest-book (commercial books) — whenever the material is a dissertation or a chapter-structured long survey. Triggers on: 'この博士論文を wiki に', 'thesis を ingest', 'dissertation を取り込んで', '修士論文を wiki 化', 'このサーベイを章ごとに wiki 化', 'SoK を ingest', 'ingest this survey', 'ingest this thesis'. Unlike wiki-ingest-book, chapter pages do NOT get `publish: false` (open-access scholarly documents)."
---

# wiki-ingest-thesis: 博士論文・長編サーベイ特化の wiki 取り込み

博士論文(修士論文含む)と長編サーベイ論文を**章単位**で wiki レイヤーに取り込む。この種の文書は「論文の出自(arXiv・大学リポジトリ・オープンアクセス)+ 書籍の構造(章立て・数百ページ)」というハイブリッドであり、骨格(章分割・並列 fan-out・機械的検証)は `wiki-ingest-book` から、出自まわり(メタデータ補完・abstract 全訳・図表全件主義)は `wiki-ingest-paper` から継ぐ。

原本は `.raw/theses/<slug>/` に不変原本として置き、**1 章 = 1 source ページ**を `wiki/sources/` に、**文書全体を表すハブ entity ページを 1 つ**(`entity_type: thesis` または `survey`)を `wiki/entities/` に作る。

> [!important] 最初に必ず読むこと
> wiki を操作する前に [`wiki/meta/conventions.md`](../../wiki/meta/conventions.md)・[`wiki/meta/japanese-style.md`](../../wiki/meta/japanese-style.md)・[`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md) を読む。**本スキルは多くの工程を [`wiki-ingest-book`](../wiki-ingest-book/SKILL.md) と [`wiki-ingest-paper`](../wiki-ingest-paper/SKILL.md) の節への参照で定義する**ので、参照された節はその場で開いて従う(要約で済ませない)。conventions と食い違ったら conventions が優先。
>
> **カタログ類は Read しない**: `wiki/index.md`・`wiki/{sources,entities,concepts}/_index.md`・`wiki/hot.md`・`wiki/log.md` の全文を `Read` してはならない。既存ページの発見は `wiki-resolve.py`、カタログ更新は `wiki-catalog.py`、本文の部分読みは `wiki-excerpt.py` に任せる。
>
> 章担当 subagent へ渡す手順は [`references/subagent-brief.md`](references/subagent-brief.md) が正本である。フル SKILL を subagent に渡さない。抽出ファイルの形は book の [`../wiki-ingest-book/references/extract-schema.md`](../wiki-ingest-book/references/extract-schema.md)。書籍側の逸話は [`../wiki-ingest-book/references/fan-out.md`](../wiki-ingest-book/references/fan-out.md)、図表の長い手順は [`../wiki-ingest-book/references/figures.md`](../wiki-ingest-book/references/figures.md)。

---

## いつ使うか(兄弟スキルとの境界)

| 入力・意図 | 使うスキル | 出力 |
|---|---|---|
| **博士・修士論文** / **30 ページ超で章相当構造を持つサーベイ・SoK** | **wiki-ingest-thesis(本スキル)** | `.raw/theses/<slug>/` + 章ごとの source + ハブ entity |
| 通常の研究論文・30 ページ未満のサーベイ | `wiki-ingest-paper` | `.raw/papers/` + source 1 枚 |
| 商業書籍・技術書・教科書 | `wiki-ingest-book` | `.raw/books/<slug>/` + 章 source + book entity |
| スライド / 動画 / 記事 | `wiki-ingest-slides` / `wiki-ingest-video` / `wiki-ingest` | 各スキル参照 |

判定基準:
- **博士論文・修士論文は常に本スキル**(ページ数によらない)。
- **サーベイ・SoK は「概ね 30 ページ超」かつ「独立した章相当のセクション構造(taxonomy の大分類が節として立っている等)」の両方を満たすときだけ**本スキル。どちらかを欠くなら `wiki-ingest-paper` で 1 source ページに収める。曖昧なら `AskUserQuestion` で「1 ページに集約(paper)か章分割(thesis)か」をユーザーに確認する。
- 論文集(proceedings)は論文単位で `wiki-ingest-paper`。

> [!important] 新しいセッションで始める
> このスキルは**新しいセッション**で起動する。`wiki-ingest-paper` から切り替えた場合、同じチャットで続けない。同じ文脈で章分割すると常駐文脈が 300k を超え `$10` 帯になる。paper セッション側は差し戻して止める。こちらは `/wiki-ingest-thesis <URL>` を新規チャットで始める。

> [!note] `publish: false` は付けない(book との明確な違い)
> 博士論文・サーベイはオープンアクセス(arXiv・機関リポジトリ)が主対象で、章 source ページは自分の言葉による要約なので、book skill と違い `publish:` フィールドを書かない。大学リポジトリ等でライセンスが明示的に厳しい(閲覧限定・転載禁止が明記されている)場合のみ、ユーザーに `publish: false` を付けるか確認する。

---

## 取り込み前のセットアップ確認

`wiki-ingest-book` の「取り込み前のセットアップ確認」と同一(Transport = filesystem 固定 / 書き込みは `wiki-page-write.py`(新規)と `wiki-append.py`(既存)に束ねる・ロックと採番はその内側 / `scripts/` は vault ルート直下 / manifest デルタ追跡)。`publish: false` を付けない点だけが book と異なる。

---

## Step 0: 原本取得と章分割

入力を PDF 参照に解決してから、`fetch-book.sh` を **`--raw-root .raw/theses`** 付きで呼ぶ。

```bash
# arXiv の abs URL / 裸 ID は PDF URL に変換してから渡す
#   https://arxiv.org/abs/2501.01234 → https://arxiv.org/pdf/2501.01234
bash scripts/fetch-book.sh --raw-root .raw/theses "<pdf-url|local-pdf-path>" "<slug>"
# 出力(key=value)は wiki-ingest-book Step 0 と同じ:
#   pdf=.raw/theses/<slug>/<slug>.pdf  txt=... toc=... chapters_dir=... chapter_NN=...
```

- slug 例: 博士論文 `phd-<姓>-<年>-<短縮題>`(例 `phd-sigelman-2021-tracing`)、サーベイ `arxiv-<id>`。
- **取得前に既存 source と照合する**: `python3 scripts/paper-ids.py check "<arxiv-url|doi>" --compact`。`HIT` なら同じ論文が既に(多くは 1 枚ものの paper source として)wiki にある。章分割で取り込み直すときは、旧ページの `related:` と被リンクを章ページやハブ entity へ張り替えてから旧ページを削除するか、残す理由を旧ページ冒頭に書く。放置すると lint の `## Paper IDs` に「1 枚ものと章分割の併存」として出続ける。
- ダウンロードが egress 拒否で失敗したらサンドボックス無効化で再実行(`wiki-ingest-paper` の「サンドボックスとネットワーク」参照)。
- **章検出の確認・`--chapters` 手動指定・アウトラインが無い PDF からの章境界復元・OCR スキャン PDF の扱いは、`wiki-ingest-book` Step 0 の「章検出の確認と失敗時のフロー」「アウトラインが無い PDF から章境界を復元する」にそのまま従う。**

thesis/survey 固有の注意:

- **前付が長い**。表紙・審査委員・謝辞・abstract・目次・図表一覧で数十ページある。abstract はハブ entity に置く(Step 0.2)ので章 source 化しない。book と同じ流儀で、序文相当や付録は本編の最大章番号より大きい捨て番号・別番号で切り出しておくと Step 0.3 の判断が楽になる。
- **LaTeX 生成 PDF はアウトラインが正確**なことが多く、`get_toc()` がほぼそのまま使える。誤検出フローに入る前に、まず `chapter_NN=` を目次と突き合わせるだけで確定することが多い。
- **サーベイは level 1 のセクション(`1 Introduction`, `2 Taxonomy`, …)を「章」として扱う**。`Chapter` という語は現れないので、章検出が 0 件なら `toc.txt` の level 1 から `--chapters` を組み立てる。
- **付録(Appendix)の扱いは Step 0.3 でユーザーに確認**する。博士論文の付録は証明・追加実験・調査票で source 化する価値があることが多い(book の「資料リスト・索引は対象外」とは事情が違う)。

## Step 0.2: メタデータと abstract(paper 由来)

- **arXiv**: `https://arxiv.org/html/<id>` または abs ページを WebFetch で取得し、タイトル・著者・abstract を裏取りする(`wiki-ingest-paper` の「メタデータと abstract の取得」と同じ)。
- **大学リポジトリ・ProQuest**: 文書のランディングページで書誌(大学・学位授与年・指導教員・DOI/handle)を確認する。実在 URL のみ採用。
- **abstract は一文ずつ忠実な日本語全訳**を作る(常体。要約・補完をしない)。置き場所はハブ entity の `> [!abstract]` callout(Step 2)。

## Step 0.3: 章リスト確定と事前決定(オーケストレータ)

`wiki-ingest-book` Step 0.3 の 7 項目(章リスト確定・全章ページ名の事前決定・バッチ日付の固定・ハブ entity と著者 entity の骨格先行作成・印字ノンブル方針・共通 briefing 1 ファイルと 3 章以上では同じスクラッチの `extracts/`・画像の章別振り分け)を**そのまま踏襲**する。「book entity」は「ハブ entity」に読み替える。thesis/survey 固有の追加:

1. **章 ↔ 既出版論文の対応表を作る**。博士論文の各章はしばしば出版済み論文の拡張版で、冒頭の "This chapter is based on ..." 宣言・章末注・序章の publications リストに対応が明記される。まず原本を grep して対応を確定し、次に wiki に ingest 済みの source があるかを照合する:
   ```bash
   grep -n -i "based on\|previously published\|appeared in\|の内容は.*発表" .raw/theses/<slug>/chapters/ch-*.txt | head
   ls wiki/sources/ | grep -i "<著者姓\|論文タイトルの一部>"
   ```
   対応表(章番号 → 出版論文 → wiki 内 `[[@...]]` の有無)を briefing に含める。担当 subagent は該当章の `related:` に既存 paper source を追加し、**論文版と thesis 版で数値・主張が食い違ったら `> [!contradiction]` callout を両ページに立てる**(thesis 版は実験の拡張・訂正を含むことがあり、矛盾は最高シグナル)。
2. **サーベイでは引用文献ごとの entity を作らない**規律を briefing に明記する。サーベイの引用は数百本あり、機械的に entity 化すると爆発する。entity 化するのは**本文で節を割いて論じられる代表システム・データセット・ベンチマーク**だけ。それ以外の引用文献は本文中の言及にとどめる。

## Step 0.5: 画像方針(book の規模制御 + paper の全件主義)

方式・手順はすべて `wiki-ingest-book` Step 0.5 に従う(300 ページ超は一括抽出せずクロップのみ / 総点数の事前 grep / 機械的な切り出し規則が立つならオーケストレータ 1 パス / `get_image_rects()` 優先とキャプション判別 / スキャン PDF の警告)。読み替えと固有事項:

- パスは `.raw/theses/<slug>/` に読み替える。attachment は `wiki/sources/_attachments/<slug>/ch<NN>-fig<N.M>-<内容>.png`(`token`・`secret` 等を含む名前の禁止も同じ)。
- 選定基準は paper 系の全件主義: **本文が参照している図表は、除外リスト(`wiki-ingest-paper` Step 0.5 §2)に当たるもの以外すべて**章スコープで埋め込む。
- **サーベイの taxonomy 図・比較表・年表は本スキルの主眼**であり、必ず取り込む。LaTeX PDF の図はベクター描画が多く `image-*.png` に出ないことが普通なので、キャプション座標クロップ(`wiki-ingest-paper` Step 0.5 §1.5)を最初から想定しておく。洋文サーベイはキャプションのコロン記法(`Figure 3: ...`)で分離できることが多い(book §1.7 の tip)。
- 埋め込みは本文近傍・図表専用セクション禁止(共通ルール)。

---

## 並列 fan-out(3 章以上は読み手と書き手を別起動)

**プロトコルは `wiki-ingest-book` の「並列 fan-out」節と同一**(3 章以上は読み手と書き手を**別起動** / extract は読み手用 briefing と同じスクラッチの `extracts/ch-NN.md` / **extract を `.raw` に置かない** / 書き手起動前に `python3 scripts/wiki-extract-check.py --forbid-raw extracts/*.md` を 1 回かける / **1〜2 章は 1 体可** / briefing は読み手用と書き手用に分ける / git 操作の明示的禁止条項 / ローリング 3〜4 体 / ハブ concept を奪い合う章の直列化は書き手に適用 / 先行章が触った concept の申し送り / 報告の `git status` 照合 / 短い章の 30〜50 行は書き手用)。「book entity」は「ハブ entity」に読み替える。スキーマは [`../wiki-ingest-book/references/extract-schema.md`](../wiki-ingest-book/references/extract-schema.md)。置き場の例: `/tmp/thesis-<slug>/extracts/ch-NN.md`。**subagent の個別プロンプトにはフル SKILL を渡さない。** 読み手には brief の読み手節、書き手には書き手節。固有事項:

- **研究章(contribution chapter)は 1 章が論文 1 本相当に重い**。40 ページ超の章には、読み手へ「読了は分割してよいが省略しない」と書く。書き手の本文は 300 行上限、深掘りは concept へ逃がす。
- **ハブ entity は subagent 禁止**(オーケストレータのみが書く。読み手も書かない)。**ハブ entity は最初から `entity_tier: full`**。共有カタログ(index・hot・log・manifest)も subagent 禁止。fan-out 完了後、**オーケストレータだけが `wiki-catalog.py`**(prepend-log / prepend-hot / prepend-changelog / add-catalog-line / prepend-master)を呼ぶ(Read/Edit しない)。
- 書き手用 briefing には対応表(Step 0.3-1)と引用文献 entity 禁止規律(Step 0.3-2)を必ず含める。読み手用には入れない。

---

## Step 1: 章 source ページを作る(`wiki/sources/`)

3 章以上では書き手が extract と書き手用 briefing から組む。章本文の全文 Read はしない。ピンポイント Read は 1 章あたり最大 2 箇所、各 80 行。超えるなら読み手やり直し。**1〜2 章は 1 体可**で、そのときは従来どおり自章テキストを全部読んでから書く。斜め読みしない。

### ファイル名(`@` プレフィックス必須)

```
wiki/sources/@YYYY__SOURCE__Title - Chapter N 題.md
```

- `SOURCE`: 博士論文 = `PhD`、修士論文 = `MSc`、サーベイ = 通常の媒体略号(`arXiv`・`CSUR` 等)。
- 例: `@2023__PhD__Learned Systems for Cloud Resource Management - Chapter 3 Workload Forecasting.md`
- 付録は `Appendix A 題`。`YYYY` は学位授与年(サーベイは発表年)。`@` の理由と参照リンク規約は conventions §4。

### frontmatter(conventions §2/§3 準拠)

`wiki-ingest-book` Step 1 の frontmatter と同型で、次を読み替える:

```yaml
sources:
  - "[[.raw/theses/<slug>/chapters/ch-NN.txt]]"
source_type: thesis          # 博士・修士論文。サーベイは paper
related:
  - "[[<題名>]]"             # ハブ entity(必須)
  - "[[@YYYY__SOURCE__対応する出版論文]]"   # 対応表にあれば
# publish: は書かない(冒頭の note 参照)
```

### 本文テンプレート(章の性質で 3 型を使い分ける)

章がどの型かは章題と内容から判断する。迷ったら研究章型に寄せる。いずれも Navigation 行から始める:

```markdown
> 前: [[@...Chapter N-1...]] | 次: [[@...Chapter N+1...]] | 全体: [[<題名>]]
```

**1. 研究章(contribution chapter)** — 提案・実験を含む章。`wiki-ingest-paper` テンプレの簡略版:

```markdown
## 要約
## 問題設定
## 提案手法
## 実験と結果
## 考察・限界
## 関連
```

**2. サーベイ章・関連研究章** — 分類と比較が主体の章:

```markdown
## 要約
## 分類軸(taxonomy)
- 分類の軸と各カテゴリの定義。taxonomy 図をここに埋め込む。
## 代表手法・システムの比較
- 比較表は Markdown 表に忠実に転記(paper Step 0.5 §2 の表の扱い)。
## 傾向と未解決課題
- 章が明示する open problems・研究動向。
## 関連
```

**3. 導入・結論・背景章** — `wiki-ingest-book` の章テンプレと同じ(要約 / 主要概念 / 主要主張 / 関連)。

共通規律(両 skill と同じ): 100〜300 行上限・図表埋め込み行は削減対象にしない / **出典に無いことは書かない** / 各主張は章本文・図表に遡及可能 / 出典検査はチャット報告(ノート本文に「検証パス」節を書かない)/ 出典表記は節番号、無ければ印字ノンブル。

---

## Step 2: ハブ entity ページ(必須)と、その他 entity

### ハブ entity

文書全体を表す entity を `wiki/entities/<題名(原語)>.md` に 1 つ作る。`entity_type: thesis`(博士・修士)または `survey`。**ハブ entity は最初から `entity_tier: full`**。frontmatter は book entity(`wiki-ingest-book` Step 2)と同型で entity_type だけ読み替える。本文テンプレート:

```markdown
# <題名>

## 概要
文書が何であり、なぜ重要かを 2〜3 文。

> [!abstract] 概要(abstract の日本語訳)
> {一文ずつ忠実に和訳(Step 0.2)}

## 書誌情報
- 著者 / 大学・学位(または媒体)/ 年 / 指導教員(あれば)/ DOI・arXiv ID・URL / 構成(全 N 章)

## 構成と主要テーマ
### Part I: ...(第 1〜M 章)     ← Part 構成があれば
→ [[@...Chapter 1 ...]] — 章の一行要約(subagent 報告から組み立てる)

## 元になった出版論文
| 章 | 出版論文 | wiki |
|---|---|---|
| Ch.3 | Author+, NSDI'22 | [[@2022__NSDI__...]] |
| Ch.4 | Author+, arXiv'23 | (未取り込み) |

## 位置づけと影響

## 関連

## 出典
```

- 「元になった出版論文」節は Step 0.3-1 の対応表から書く。対応が 1 件も無い文書(サーベイ等)では節ごと省略する。
- 骨格の先行作成・序文由来の記述の出典表記・fan-out 後の自己点検は `wiki-ingest-book` Step 0.3-4 / Step 2「序文由来の記述の出典表記」に従う。
- 既存 `notes/`・`books/` との basename 衝突時のパス修飾は conventions §9-7 を準用する。

### その他 entity

著者・大学・組織・代表システム・データセットは `wiki-ingest-paper` Step 2 の規約どおり(原名・`entity_type`・`first_mentioned` は `@` 付き・address 採番)。既存の有無は `wiki-resolve.py entity:"<名1>" entity:"<名2>" ... --compact` で**登場する全 entity 名を 1 回の呼び出しで**確認する。**共著者・一度きりの言及は `entity_tier: stub`**。**サーベイの引用文献は entity 化しない**(Step 0.3-2)。

---

## Step 3: concept ページ(`wiki/concepts/`)

conventions §8 のとおり(本文は主題別の節で再編纂が書く。ingest の追記先は `## 未編纂の観察`(旧名 `## 横断的知見` が残るページではそこ)と `## 未解決の問い` の 2 節。積み増し原則、contradiction callout。追記の前に `wiki-excerpt.py --outline` で既存の命題を見る)。既存 concept の有無は `wiki-resolve.py concept:"<候補1>" concept:"<候補2>" --compact` で候補語をまとめて確認し、更新時は触る concept を並べた `wiki-excerpt.py P1 P2 --tail 15 --budget-tokens 1800` で必要節だけ読む。`--total-budget` を使うなら `N * --budget-tokens` 以上にする。更新上限 5 なら 9000 以上。切れたページは読んだことにしない。**1 文書バッチ(文書全体)で concept は新規最大 3、更新最大 5。** 章ごとにリセットしない。溢れは log の `Deferred:` に残す。台帳にも積む: `python3 scripts/concept-candidates.py add --name <候補> --source "[[@<今回の source>]]" --reason "上限超過"`。`wiki-resolve.py` が `ledger:<名>(<k> docs, pending)` を返した候補は既に保留中なので同じコマンドで言及を足し、`ready`(2 文書以上)なら今回の新規枠で優先して新設し `promote` する(conventions §12 ルール 5)。新設か保留かで迷う候補は `python3 scripts/wiki-profile.py`(研究関心の要約 25 行、profile 全文は読まない。終了 3 なら関心判定を飛ばす)に照らす。対象外の用語は concept にせず source に留め、コア関心の候補は 2 文書目で優先して新設する(conventions §12 ルール 3)。固有の注記:

- **サーベイの taxonomy 大分類は concept ページ候補の宝庫**であり、本スキルの主要な収穫。大分類 1 つが既存 concept と重なるなら積み増し、独立主題なら新設する。
- サーベイの「未解決課題」節は、触れた concept の `## 未解決の問い` にそのまま還流させる(出典付き)。
- 同一文書の複数章は独立ソース扱いでよい(章 A と章 B の突き合わせも受信箱に書ける)。文書全体で複数章の突き合わせが揃った新設 concept は、主題節を最初から立ててよい(見出しは主題の名詞。「知見」「観察」「考察」「まとめ」「横断的」を使わない)。

---

## Step 3.5〜5: 機械的検証・索引更新・再取り込み判定・commit

`wiki-ingest-book` の Step 3.5(図の両方向照合・Navigation 解決・frontmatter 表・枚数)、Step 4(`git status` からの機械的一覧生成・他セッション混在の分類・**`wiki-catalog.py` による log/hot/changelog/catalog 更新(Read/Edit しない)**・manifest 更新)、「再取り込み判定と増分章追加」、Step 5(バッチ 1 commit・厳密照合)を**そのまま踏襲**する。差分のみ:

- Step 3.5 の検証コマンドは `python3 scripts/wiki-verify-ingest.py --glob 'wiki/sources/@YYYY__SOURCE__Title - Chapter*' --attachments-slug <slug> --expect-count N --date YYYY-MM-DD --require-related '[[<ハブ entity 名>]]'`。**`publish: false` は付けないので、`FM-PUBLISH` の warn が出たら惰性で付いた印**として外す。
- frontmatter 検査項目: `publish: false` の代わりに **`source_type`(thesis|paper)と `related:` のハブ entity リンク**を全章検査する(前者は `FM-STYPE`、後者は `--require-related` が全章分を `FM-RELATED` で見る)。
- log ラベル: `## [YYYY-MM-DD] ingest-thesis | <題名>`。hot にはハブ entity + 代表章 2〜3 件のみ。
- manifest キー: `.raw/theses/<slug>`(形式は book と同じ。`chapters_count`・`chapter_pages` を持つ)。
- commit 前に `wiki-retrieve-refresh.py --pages <作成・更新パス> ... --no-llm` を必ず実行する。
- commit メッセージ: `wiki: ingest-thesis | <題名>`。

コミット後に `python3 scripts/usage-report.py --self --log-line` を実行し、出力の 1 行を**チャットの完了報告に含める**(`wiki/log.md` には書かない。log エントリはセッション終了前に書かれるため数値が確定しない)。`--latest` は並行 ingest で他人を拾うので使わない。環境変数が空なら `--session <このセッションの ID>`。振り返りには `--since YYYY-MM-DD` を使う。章ごとに subagent へ委譲した場合、subagent 側の使用量は親ログに含まれないため、この値はオーケストレータ側だけのものになる。

---

## やってはいけないこと

共通則(`wiki-ingest-book`「やってはいけないこと」の全項目を、パスと entity 名を読み替えて適用する)に加えて:

- **`scripts/allocate-address.sh` を直接実行しない。** 採番はヘルパー外では失敗する。`wiki-page-write.py` がロック内で行う。`cd` だけの Bash も禁止。
- **新規 source / entity / concept を `Write` ツールで作らない。** 既存なら append。衝突したらマージ復旧せず止める。
- **シートで番号が付いた attachment を Read しない。** `individual-reads` はシートを除く画像 Read の実数。
- **`wiki-ingest-paper` セッションの続きで本スキルを完走しない。** 新セッションで始める。
- **完了報告の計測に `--latest` を使わない。** `--self`(失敗時だけ `--session <ID>`)にする。
- **30 ページ未満のサーベイ・通常論文を勝手に章分割しない**(`wiki-ingest-paper` の領分)。逆に博士論文を 1 source ページに押し込まない。
- **サーベイの引用文献を機械的に entity 化しない**。節を割いて論じられる代表物のみ。
- **読み手と書き手を同一 subagent で回さない**(3 章以上)。別起動する。**1〜2 章は 1 体可**。extract を `.raw` に置かない。読み手に wiki を書かせない。書き手起動前に `python3 scripts/wiki-extract-check.py --forbid-raw extracts/*.md` を 1 回かける。`QUOTE-HEAVY` でも書き手を起動しない。
- **ハブ entity・共有ファイルを subagent から書かない**(オーケストレータのみ)。
- **章 ↔ 出版論文の対応を照合せずに fan-out しない**。wiki 取り込み済みの論文版との接続と矛盾検出は本スキル固有の価値であり、後付けでは漏れる。
- **`publish: false` を惰性で付けない**(book の規律を持ち込まない)。付けるのはライセンスが明示的に厳しい場合にユーザーが承認したときだけ。
- 全ページレンダリング禁止・目次だけから未読章を書かない・報告鵜呑み禁止・広い `git add` 禁止(いずれも book と同じ)。
