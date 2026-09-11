---
type: meta
title: "Wiki Conventions"
created: 2026-06-02
date: 2026-06-02 18:46
updated: 2026-09-06
tags:
  - 2026/06/02
  - meta
  - conventions
  - 2026/08/13
  - 2026/08/15
  - 2026/08/23
  - 2026/08/30
  - 2026/09/02
  - 2026/09/06
status: evergreen
related:
  - "[[index]]"
  - "[[overview]]"
  - "[[token-discipline]]"
---

# Wiki Conventions(既存 vault 規約の吸収レイヤー)

Navigation: [[index]] | [[overview]]

> [!important] wiki-ingest / wiki-query / wiki-lint / save / autoresearch を実行する前に必ずこのファイルを読むこと。
> カタログ・ホット・ログの全文は読まない。発見・節抽出・追記は [[token-discipline]](`wiki/meta/token-discipline.md`)に従う。
> claude-obsidian 標準のページ形式は、この vault の既存規約(`papers/`・`research/`・`structures/` で確立済み)に合わせて以下のとおり**上書き**する。標準と矛盾する箇所は**このファイルが優先**する。

---

## 1. 言語

- すべて**日本語・常体(だ・である調)**で書く。
- 箇条書きは体言止め可。
- 技術用語・コード識別子・固有名詞は原語のまま。
- **英語表現は「原語のまま残す/カタカナにする/漢語に訳す」のいずれかに必ず寄せ、和文に英単語が素のまま混ざるコードスイッチングを避ける**(例:「multi-modal な observability データ」→「マルチモーダルなオブザーバビリティデータ」)。keep/カタカナ化/漢語化の方針と用語集は [[japanese-style]](`wiki/meta/japanese-style.md`)に従う。ingest・整文の前に必ず一読する。
- 既定はカタカナ(agent→エージェント, telemetry→テレメトリ 等)。fault→障害, localization→箇所特定, detection→検知, mitigation→緩和 など定着した漢語は漢語にする。略語・固有名詞・数式・wikilink は原語のまま。初出の専門用語は「和訳(原語)」の併記が望ましい。

## 2. frontmatter(全 wiki ページ共通)

標準スキーマ(`type` / `title` / `created` / `updated` / `status` / `related` / `sources`)を維持しつつ、既存規約の `date` と日付タグを**必ず併記**する。flat YAML のみ(ネスト禁止)。

```yaml
---
type: source            # source|entity|concept|question|thesis|comparison|survey|meta
title: "人間可読タイトル"
date: 2026-06-02 18:46   # 既存規約(必須): 作成日時 YYYY-MM-DD HH:mm
created: 2026-06-02      # wiki 標準: YYYY-MM-DD
updated: 2026-06-02      # wiki 標準: 編集のたびに更新
aliases: []             # 既存規約: 別名(検索・バックリンク用)
tags:
  - 2026/06/02          # 既存規約(必須・最優先): 日付タグを先頭に
  - source              # type タグ(source|entity|concept...)
  - <domain-tag>        # 任意: distributed, aiops 等
status: seed            # seed|developing|mature|evergreen
related: []
sources: []             # "[[.raw/...]]" または "[[ソース要約]]"
---
```

ルール:
1. `tags` の**先頭は必ず日付タグ `YYYY/MM/DD`**。続けて type タグ、domain タグ。
2. `date`(時刻付き)と `created`/`updated`(日付のみ)は両方持つ。`updated` は編集ごとに更新。
3. wikilink を YAML に書くときは必ずクォート: `"[[Page Name]]"`。
4. リストは `- item` 形式(インライン `[a,b]` 禁止)。

## 3. type 固有フィールド

```yaml
# source
source_type: paper      # article|video|podcast|paper|book|slides|transcript|data|thesis
author: ""
date_published: YYYY-MM-DD
url: ""
arxiv_id: "2405.16444"  # paper: arXiv の版番号なし ID。url / .raw slug / 本文から機械導出できる(scripts/paper-ids.py backfill)
doi: "10.1145/3689031.3696098"  # paper: 小文字 DOI(doi.org/ は付けない)。arXiv しか無い論文には付けない
confidence: high        # high|medium|low
key_claims: []

# entity
entity_type: person     # person|organization|product|repository|place|dataset|book|thesis|survey
entity_tier: stub       # stub|full。初出の共著者は stub。2 ソース目以降またはハブは full。既存本文ページは無指定でも full とみなす
role: ""
first_mentioned: "[[ソース要約]]"

# concept
complexity: intermediate # basic|intermediate|advanced
domain: ""
recompiled: YYYY-MM-DD   # 最終再編纂日(§8 更新ルール 6)。再編纂だけが更新する。未再編纂のページには無い

# thesis(wiki/questions/ に置く。wiki-thesis が書く命題の判定ページ)
thesis: "命題の逐語"
question: "元の問いの逐語"     # 問いから命題を起こしたときだけ
verdict: supported       # supported|partially|contradicted|insufficient|mixed
confidence: medium       # high|medium|low。直接の根拠 3 本以上で high、1〜2 本で medium、間接だけなら low
origin: "[[concept]]"    # `## 未解決の問い` から起こしたときの出自ページ
judged: YYYY-MM-DD       # 判定日。判定を見直したら更新する
```

thesis ページの判定は「命題が真か」ではなく「wiki のソースが命題をどこまで支持するか」である。本文は 根拠表(側 / 要約 / 出典 / 強さ)・機序・判定・反証条件・差し戻し の節を固定で持つ。insufficient は `autoresearch` / `wiki-ingest-*` への差し戻しであり、web で埋めない。手順は `wiki-thesis` skill。

## 4. ファイル名

| 種別 | パス | 命名 | 例 |
|---|---|---|---|
| source | `wiki/sources/` | **`@YYYY__SOURCE__Title.md`**(先頭に必ず `@`。`route` の slug 出力でなくこちらを優先) | `@2026__MLSys2026__Amin Vahdat Keynote - The Architecture of Intelligence.md` |
| entity | `wiki/entities/` | 原名(大文字・スペース保持。`route entity` の出力どおり) | `Amin Vahdat.md` |
| concept | `wiki/concepts/` | 原名(`structures/` の MOC 名と揃える) | `異常検知.md` |

- ファイル名のセパレータは既存どおりアンダースコア 2 つ `__`、スペースは保持、禁則文字(`/` `:` 等)は空白で囲む。
- 章分割する長編文書の章 source は `@YYYY__SOURCE__Title - Chapter N 題.md`(書籍は §9、博士論文・長編サーベイは §10。博士論文の `SOURCE` は `PhD`、修士は `MSc`)。
- **source は種別を問わず(paper / article / video / podcast / slides / transcript / data など全て)先頭に `@` を付ける**。理由: 既存 `papers/` が同形式 `YYYY__SOURCE__Title.md` を使うため `@` がないと basename が衝突し、Obsidian の `shortest path when possible`(既定)でベアリンク `[[YYYY__SOURCE__Title]]` が**より浅い `papers/` 側に誤解決**する。`@` で source を一意化し、`[[@YYYY__SOURCE__Title]]` で wiki/sources に確実にリンクさせる。
- したがって **source を参照する wiki 内リンクは全て `@` 付き**で書く(frontmatter 自己参照・source 間リンク・index/log/hot・entity/concept からの参照すべて)。`papers/` の単一ソース詳細メモを**意図的に**指す箇所だけ `[[papers/YYYY__SOURCE__Title|...]]` とパス修飾する(§6)。
- entity / concept を原名にするのは、既存 `structures/*.MOC.md` 内の `[[異常検知]]` 等と名前空間を一致させ、相互リンクを成立させるため(source の `@` は逆に `papers/` との名前空間を**意図的に分離**するためで、目的が異なる)。

## 5. 出典(provenance)厳格性

- 既存の会議ノート規約を踏襲。すべての claim はソースに遡及可能であること。
- 出典の優先度: **Slides > Official Page > Audio/Video Transcript > Extracted PDF Text**。
- source ページの `key_claims` と本文の主張は、`sources:` frontmatter・`## 出典`・raw ソースへのリンクから遡及可能にする。source 自身に由来する本文と画像キャプションには、冗長な `(Source: …)` インライン引用を付けない。外部ソースとの比較、矛盾、source 自身から遡及できない主張には明示的な出典リンクを付ける。entity / concept ページの出典表記はこの省略ルールの対象外である。
- 矛盾は黙って上書きせず `> [!contradiction]` callout で両ページに明示。
- 矛盾の索引は `python3 scripts/contradiction-index.py --write` が [[contradictions]](`wiki/meta/contradictions.md`)に生成する(lint が再生成する。手で編集しない)。状態(未決着 / 説明済み)は本文の見出し語からの推定であり、callout 本文に `(status: explained)` または `(status: open)` を書けば上書きできる。片側にしか callout が無い頁対は lint が候補として報告する。query は `--query <語>...` で「X と Y は矛盾するか」に索引を使う。
- 主題節の命題は**再検証**の対象である。`python3 scripts/claim-audit.py sample` が命題と、命題が引く source ページの該当段落を対にして出し、判定(supported / unsupported / not_in_source / source_missing / unclear)を台帳 `.vault-meta/claim-audit.json` と `wiki/meta/claim-audit-YYYY-MM-DD.md` に残す。出典に無い・食い違う命題は黙って直さず、`- 留保: 再検証 YYYY-MM-DD — …` を足すか、再編纂の痕跡(§8 更新ルール 4)を残して命題を弱める。手順は `wiki-refactor` の `references/claim-audit.md`。

## 6. 既存資産との橋渡し(一方向参照の原則)

- wiki ページから既存 `papers/`・`research/`・`structures/*.MOC.md` へは `[[...]]` で**一方向参照**してよい。
- **既存ファイル(papers/・research/・structures/・notes/)は wiki-ingest で書き換えない**。MOC への逆リンク追記は**人間が承認したときのみ** 1 件単位で行う。
- concept ページは関連する `structures/*.MOC.md` を参照し、発見性を担保する。役割の違い: MOC=人間がキュレートする読み筋、concept=LLM が新規ソースから積み上げる定義・関係の集約(重複は許容)。
- wiki ページの一望は種別ごとの Obsidian の Base である。[[concepts.base]] / [[sources.base]] / [[entities.base]]。各フォルダに `type` が揃ったページがあればライブで現れる。ingest はこれらの Base を更新しない。各 `_index.md` のカタログは取り込み履歴用であり、地図ではない。

## 7. ページ構成・分量

- 標準テンプレ(`$PLUGIN/_templates/{source,concept,entity}.md`)の見出し構成をベースに、本文は日本語常体。
- **source / entity ページ**: 1 ページ 100〜300 行を上限とする。超えたら分割する。source は 1 ソース = 1 ページで増え続ける性質がないため、上限として運用してよい。
- **concept ページ**: 300 行は**上限ではなく分割を検討する目安**とする。concept は ingest のたびに積み増される性質を持ち、行数を理由に知見を捨てると §8 の積み増し原則が壊れる。**§8 の積み増し原則が行数の目安より優先する**。
  - 300 行に近づいたら、追記を止めるのではなく「**独立した主題を切り出せないか**」を検討する合図として扱う。切り出せるなら新しい concept ページへ分割し、元ページからはリンクで繋ぐ(例: [[サービスレベル目標]] から [[SLO目標値の選定]]・[[意味のあるSLI設計]] を切り出した)。厚いのが `## 未編纂の観察` だけなら、分割ではなく再編纂(§8 更新ルール 4・6)で主題節へ畳む。
  - 切り出す主題が見当たらないなら、**300 行を超えて積み増してよい**。目安の超過それ自体は問題ではない。
  - **やってはいけないこと**: 行数を理由に `## 未編纂の観察` や `## 未解決の問い` への追記を見送ること。ingest で得た知見を落とすくらいなら超過を許容する。分割も再編纂も後からできるが、書かれなかった知見は復元できない。
  - 分割の単位は行数ではなく主題の独立性で決める。500 行でも 1 つの主題として一貫していれば分割しなくてよいし、200 行でも明らかに別主題が同居していれば分割してよい。

## 8. concept ページの構成(主題節・未解決の問い・未編纂の観察)

concept ページは、複数ソースを横断した知識を compile した**参照文書**である。本文は**主題別の節**で構成し、ingest が投げ込む観察は固定名の受信箱 `## 未編纂の観察` に受け、再編纂(更新ルール 4・6)で主題節へ畳み込む。読者(人間と、query 時の LLM)は主題節だけを読めば現在の理解を得られ、受信箱は読み飛ばせる。物語的な長編は concept ではなく `wiki/surveys/`(`wiki-survey`)の役割で、concept は Wikipedia の項目のように主題別に節立てされた参照文書を目指す。

固定見出しは次の 6 つで、いずれも機能名である(内容の同語反復ではない)。`## 未解決の問い` と `## 未編纂の観察` は空でも見出しを置く。主題節はページごとに自由に立てる。

```markdown
## 定義
[この概念が何か。常体・現在形・1 段落。出典付き。
 主題節が 3 つ以上あるハブでは、2 段落目に現在の理解の要約(合意・争点・分野の移動)を置く。この要約段落は再編纂だけが書き換える。]

## 子概念
[ハブのときだけ。子への wikilink と一行の役割。無ければ見出しごと省略してよい。]

## <主題節 1>
## <主題節 2>
## ...
[ページごとに自由。2〜7 節。命題を単位に書き、根拠を入れ子の箇条書きで従える(主題節の規則 2)。]

## 未解決の問い
- 次に調べるべき問いを箇条書きで蓄積する作業リスト。
- 解決したら本節から落とし、知見になったものは主題節へ、独立に答える価値があれば `wiki/questions/`(単発回答)または `wiki/surveys/`(長編編纂)へ昇格させる。問いを**判定で閉じる**手続きは `wiki-thesis`(`type: thesis`。問いを命題に直し、支持 / 反対 / 機序 / メタ / 隣接で根拠を集めて判定する)。judged 済みの問いは本節から落とし、`## 未編纂の観察` に判定ページへのリンクつきで 1 行残す(主題節へ畳むのは再編纂)。insufficient の問いは落とさず、判定ページへのリンクと不足を末尾に足す。

## 未編纂の観察
- ingest の**唯一の追記先**。新ソースと既存ソースの突き合わせで見えた観察を、対比のまま末尾へ積む。
- 各項目は根拠を `(Source: [[A]], [[B]])` で明示。矛盾なら `> [!contradiction]` を併記してよい。
- 読者向けではない。再編纂で主題節へ畳み込まれて消える。

## 関連
- 関連 source / entity への wikilink、関連 `structures/*.MOC.md` への一方向参照(§6)。

## 出典
- 
```

主題節の規則:

1. **見出しは主題の名詞にする。** 読者が検索語にできる名詞句(「verifier の保証範囲」「成熟モデルの 2 軸」)だけを許す。ページの生成過程を指す語 — 「知見」「観察」「考察」「示唆」「まとめ」「その他」「補足」「横断的」 — を見出しに使わない。concept はそれ自体が横断の産物なので、節名で横断を名乗るのは同語反復であり、汎用の受け皿は羅列を招く。
2. **命題を単位に書く。** 各箇条書きの先頭は「何が真か」を述べる太字の一文(命題)。用語ラベルを命題にしない。対比(A と B を並べると X が見える)は命題ではなく根拠であり、入れ子の箇条書きに `- 根拠: [[@A]] — 一行` の形で従える。反証・留保・横参照も同じ入れ子に `- 反証:` / `- 留保:` / `- 関連:` で並べる。命題行だけを読めば節の要約になり、開けば根拠になる(Obsidian の折り畳みが効く)。1 節の命題は 3〜8 件を目安にし、10 件を超えたら主題が同居していないので分ける。7 節上限よりこの分割を優先する。
3. **ページの自己言及を書かない。** 「既出」「本ページが集約してきた」「本ページが蓄積する」「本ページが扱う」「本頁に初めて追加する」のように、ページ自身の蓄積履歴を語る文は読者向け本文に置かない(主題節だけでなく `## 未解決の問い` も含む)。「本 wiki の X 層が観測した」のように wiki 自身を命題の主語にもしない(命題は対象世界について述べる)。比較の履歴は `wiki/log.md` が持つ。
4. **出発点の雛形は 3 つ**: 歴史軸(前史 → 転回 → 現在)、問題構造軸(問題 → 解決 → 限界)、合意軸(合意 → 論争 → 未解決)。ただし雛形より**同じ domain の既存 concept の節構成に倣う**ことを優先する。近隣ページの構成が揃っていると query 時の読み比べが楽になる。
5. **主題節が 7 つを超えたら親子化(§13)を検討する。** 節が増えすぎるのは別主題が同居している合図。ただし 1 節 10 命題超を 7 に収めるために押し込むくらいなら、8 節以上にして親子化候補として報告する。
6. **単一ソースだけで言える事実はページに書かない**(定義の出典を除く)。それは source ページに置く。§12 の新設閾値と同じ規律をページ全体に適用する。従来 `## 横断的知見` の節規則だったものを、ページの規則に格上げした。
7. **新設時に既に 2 ソース以上の突き合わせがあるなら、主題節を最初から立ててよい**(書籍・thesis の章横断など)。1 ソース目だけなら主題節は作らず、`定義` と `未解決の問い` から育てる。
8. **旧見出し `## 横断的知見` は `## 未編纂の観察` の旧名として扱う。** 既存ページを一斉に改名しない。ingest は旧見出しがあればそこへ追記し(スクリプトは旧名を別名解決する)、再編纂に入ったページだけが主題節へ畳み込まれて旧見出しが消える。空欄 placeholder 1 行だけの旧見出しは lint で機械的に `## 未編纂の観察` へ改名してよい。

更新ルール(全 ingest skill 共通):

1. **新しいソースが ingest されるたびに、§12 の上限内で触れた concept の `## 未編纂の観察` と `## 未解決の問い` を更新する**。上限を超えた候補は log の `Deferred:` に残す。`wiki-ingest` / `wiki-ingest-paper` / `wiki-ingest-slides` / `wiki-ingest-video` / `wiki-ingest-book` / `wiki-ingest-thesis` / `autoresearch` のいずれも対象。
2. 上限内で concept に触れたら、(1) **未編纂の観察**に新ソースと既存ソースの突き合わせで見えた観察を追記し、(2) **未解決の問い**に新たな問いを追加し、解決済みの問いを落とす。追記の前に `wiki-excerpt.py --outline` で主題節と命題の地図を見て、既存の命題を補強・反証する観察なら冒頭に `[節名]` を付ける(再編纂の手掛かり)。**ingest は主題節を直接書き換えない。**
3. 1 ソース目から育て始める(観察は 2 ソース目以降に増えるのが普通だが、定義時点で見えた問いは未解決の問いに入れておく)。
4. **「黙って上書きしない」は「書き換えない」ではなく「痕跡なしに書き換えない」と読む。** ingest は既存項目を書き換えず積み増すだけ(ここは従来どおり)。既存項目の統合・言い換え・並べ替え・削除は**再編纂(recompile)の工程だけ**に許す(専用 skill ができるまでは `wiki-refactor` の一様式として行う)。再編纂は次の 3 点を痕跡として必ず残す: (a) git の履歴(1 再編纂 = 1 コミット)、(b) `wiki/log.md` への「N 件の観察を M 件の主張へ畳み込んだ」エントリ、(c) 畳み込んだ元の箇条書きを同ページ内の折り畳み callout(`> [!note]- 編纂前の観察 YYYY-MM`)へ退避。退避 callout は次回の再編纂で削除してよい。書き換え後の各主張にも `(Source: ...)` を必ず引き継ぎ、根拠を伴わない主張を再編纂で生まない。矛盾の扱い(§5 の contradiction callout)はこの読み替えの対象外で、再編纂でも矛盾を散文に溶かして消さない。
5. **行数を理由に追記を見送らない**。concept の 300 行は上限ではなく分割を検討する目安であり、積み増し原則が優先する(§7)。ページが厚くなったら、追記を諦めるのではなく主題を切り出して分割するか、再編纂(上記 4)で畳み込む。
6. **再編纂(recompile)の発火と内容**。`python3 scripts/wiki-concept-stats.py --compile-debt` が、受信箱(`## 未編纂の観察`、旧名 `## 横断的知見` を含む)の箇条書きが 5 件以上、または主題節を 1 つも持たないまま観察が 15 件以上のページを compile 負債として列挙する。再編纂は (1) 受信箱の観察を主題節の命題へ畳み込み(既存命題の補強・反証・精緻化、または新命題)、(2) 主題節の規則 1〜6 に沿って見出しと命題文を整え、(3) ハブなら `## 定義` の要約段落を書き直し、(4) 更新ルール 4 の痕跡 3 点を残す。手順は `wiki-refactor` skill の再編纂様式に従う。再編纂は ingest の途中で行わず、独立した作業単位(1 ページ = 1 コミット)とする。負債の走査結果は `python3 scripts/recompile-queue.py refresh` が再編纂キュー(`.vault-meta/recompile-queue.json`、git 追跡)へ同期し、ページごとの状態(pending / in_progress / done / rejected / blocked / skipped)と試行履歴、人間の却下理由を持つ。対象は `next` から選び、着手時に `mark --state in_progress`、完了時に `--state done`、人間が差し戻したら `--state rejected --reason` で理由を残す(次の試行が読む)。却下 3 回で `blocked` になり、人間が `reopen` するまで候補に出ない。

## 9. 書籍(book)ソース

書籍の取り込みは `wiki-ingest-book` skill を使う。規約:

1. **章分割原則**: 書籍は章ごとに 1 つの source ページとする。命名は `@YYYY__PUBLISHER__BookTitle - Chapter N 題.md`(`PUBLISHER` は出版社の通称: `OReilly`・`Wiley` 等)。書籍全体を 1 つの source ページに押し込まない。
2. **断片例外**: 最初から書籍の断片(1 章分)が入力された場合は分割しない(source は 1 枚)。
3. **book entity 必須**: 書籍そのものを表す entity ページ(`entity_type: book`)を 1 つ作り、「構成と主要テーマ」節から各章 source へ `→ [[@...]] — 一行要約` でリンクする。取り込んだ章だけを列挙する。
4. **`publish: false` 必須**: 書籍は著作権コンテンツのため、章 source ページ(断片含む)の frontmatter に `publish: false` を入れ、Obsidian Publish への公開を無効化する。
5. 原本は `.raw/books/<slug>/` 配下(PDF・全文テキスト・`toc.txt`・`chapters/ch-NN.txt`・`images/`)。
6. 遡及変更しない: 本規約導入前の既存ページ(`SRE Book.md` 等の `entity_type: product`、SRE Book 章の `source_type: article`、`.raw/articles/sre-book-*` の原本配置)はそのまま温存する。
7. **既存 `books/`・`notes/` ノートとの basename 衝突に注意**: book entity は §4 のとおり原名(原題そのまま)でファイル名を付けるため、`books/<書名>.md` や `notes/**/<書名>.md` に同名の既存ノートがあると basename が衝突する。この場合、Obsidian の既定(shortest path when possible)はベアリンク `[[<書名>]]` を**より浅い既存ノート側に誤解決する**(§4 で source に `@` を付けている理由と同じ問題)。entity は原名を維持したまま(`@` のような改名はしない)、**その book entity を参照する wiki 内リンクは全て `[[wiki/entities/<書名>|<書名>]]` とパス修飾する**(章 source の frontmatter `related`・ナビゲーション行・`書籍:` リンク・他 entity/concept からの参照すべて)。entity ページ作成時に `books/<書名>.md` / `notes/**/<書名>.md` の存在を確認し、衝突があれば新規参照から一貫してパス修飾する。

## 10. 長編学術文書(thesis / survey)ソース

博士論文・修士論文と、30 ページ超で章相当構造を持つサーベイ・SoK の取り込みは `wiki-ingest-thesis` skill を使う。規約:

1. **章分割原則**: 書籍(§9)と同じく章ごとに 1 つの source ページとする。命名は `@YYYY__SOURCE__Title - Chapter N 題.md`(博士論文の `SOURCE` は `PhD`、修士は `MSc`、サーベイは媒体略号)。`source_type` は博士・修士論文が `thesis`、サーベイが `paper`。
2. **ハブ entity 必須**: 文書全体を表す entity ページ(`entity_type: thesis` または `survey`)を 1 つ作り、「構成と主要テーマ」節から各章 source へリンクする。博士論文では「元になった出版論文」節で章と出版済み論文(wiki 取り込み済みなら `[[@...]]`)の対応を示す。
3. **`publish: false` は不要**(§9 の書籍との違い)。オープンアクセスの学術文書が主対象で、章ページは自分の言葉による要約のため。ライセンスが明示的に厳しい場合のみユーザー承認のうえで付ける。
4. 原本は `.raw/theses/<slug>/` 配下(`.raw/books/` と同じレイアウト: PDF・全文テキスト・`toc.txt`・`chapters/ch-NN.txt`・`images/`)。
5. **適用境界**: 博士・修士論文は常に本規約。サーベイは「概ね 30 ページ超」かつ「独立した章相当セクション構造」の両方を満たすときのみ。それ未満は通常の paper source(`wiki-ingest-paper`)とする。

## 11. トークン規律(カタログを読まない)

`wiki/index.md`・各 `_index.md`・`wiki/log.md`・`wiki/hot.md` の全文は、ingest / query の入力にしない。手順とコマンドは [[token-discipline]] を正本とする。

- 既存ページの有無: `python3 scripts/wiki-resolve.py`
- 本文: `python3 scripts/wiki-excerpt.py`
- 索引・ホット・ログの更新: `python3 scripts/wiki-catalog.py`(ファイルを Read して Edit しない)
- query の第一経路: `python3 scripts/retrieve.py`(ページグラフがあれば第 3 路。`wiki-graph.py`)
- ingest 後: `python3 scripts/wiki-retrieve-refresh.py --pages ... --no-llm`(BM25 と `graph.json` を差分更新)
- 関心の要約: `python3 scripts/wiki-profile.py`(無い vault では終了 3。判定を飛ばす)
- 束: `python3 scripts/wiki-context-pack.py`(subagent への引き継ぎ)
- 機械状態: `python3 scripts/wiki-doctor.py`(lint の最初)

## 12. entity の stub と concept の新設閾値

1. **entity stub**: 初出の共著者・一度きりの人物/組織は `entity_tier: stub` で、frontmatter と所属/役割 2〜3 行だけを書く。本文節を増やさない。
2. **entity full**: 同一 entity が 2 つ目のソースに現れたら `entity_tier: full` へ上げ、通常の本文を育てる。書籍・thesis のハブ entity、繰り返し登場する組織/製品は最初から full でよい。
3. **concept 新設**: 単一ソースで閉じる用語は source ページに留める。concept を新設するのは、resolve で既存が無く、かつ 2 ソース以上にまたがるか今後またがることが明らかなときだけ。迷う候補は研究関心プロファイルの要約(`python3 scripts/wiki-profile.py`。`WIKI_PROFILE_PATH`、なければ `research/curation/profile.md`、なければ `wiki/meta/profile.md`)に照らす。プロファイルが無い vault では関心判定を飛ばす。対象外に当たる用語は concept にせず source に留め、コア関心に当たる候補は 2 文書目で優先して新設する。プロファイルは「作るか」を決める材料であり、「何を書くか」は source に従う。
4. **1 取り込みの上限**: concept は新規最大 3、更新最大 5。この上限は §8 の「触れたら更新」より優先する。溢れた候補は log の `Deferred:` に書き、次の関連ソースで育てる。書籍・thesis も文書全体でこの上限を目安にする(章ごとにリセットしない)。
5. **候補の台帳**: 溢れた候補と「2 ソース目を待つ」候補は、log の `Deferred:` に加えて `python3 scripts/concept-candidates.py add --name <候補> --source "[[@...]]" --reason "<保留理由>"` で `.vault-meta/concept-candidates.json`(git 追跡)にも積む。独立 source 数は文書単位で数える(同じ書籍の章 2 つは 1 本)。`wiki-resolve.py` が concept を見つけられずに `ledger:<名>(<k> docs, pending)` を返したら、その候補は既に保留中なので同じコマンドで言及を足し、`ready`(2 文書以上)なら今回の新規枠で優先して新設し `promote` する。concept にしないと決めたら `reject --reason`。lint が到達済み候補を報告する。
6. **entity の alias は本人・当該組織を指す名前だけ**: 英名と和名、正式名と略称(`Massachusetts Institute of Technology` と `MIT`)、綴りゆれは alias にする。姓だけ・`Y. Li` 型の引用表記は同姓の別人と衝突するので alias にしない(あっても名寄せでは無視する)。親組織のページに子組織や別ページの題名を alias として置かない(`Microsoft` に `Microsoft Research` を置くと resolve が衝突する)。同一 entity の別ページは `python3 scripts/entity-resolve.py scan` が strong / medium / weak の候補対として出し、統合は wiki-refactor の「統合」手順(残す側に旧題名を alias、wikilink を張り替え、参照ゼロで削除)で人間が承認して行う。別人の同姓同名は `entity-resolve.py decide --rejected --reason` で台帳 `.vault-meta/entity-merges.json` に記して以後の候補から外す。

## 13. concept の親子化

300 行超のハブは、行数で削るのではなく親を地図・子を論題に分ける(§7 と同じ優先順位)。

- 親ページは `## 定義` の直後に `## 子概念` を置く。子への wikilink と一行の役割だけを列挙する。
- 具体的な手法・層・評価設計は子ページに積み増す(ingest は子の `## 未編纂の観察` へ、再編纂は子の主題節へ)。
- 親の主題節には、子を 2 つ以上並べて初めて見える観察だけを残す。
- ingest でハブに当たったら excerpt で定義・子概念・主題節の outline・受信箱末尾だけを読み、更新先は最も近い子を優先する。
- 候補の列挙は `python3 scripts/wiki-concept-stats.py`。related 先がそれ自体ハブ規模(走査閾値以上)ならピアであり、子にしない。相互参照は問わない(手法の子も親へ戻す related を持つ)。薄い related 先だけを子候補とする。既存ハブの本文分割は `wiki-refactor` の対象であり、ingest の途中で親を解体しない。
