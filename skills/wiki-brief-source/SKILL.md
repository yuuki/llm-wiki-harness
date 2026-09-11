---
name: wiki-brief-source
description: "ingest 済みの 1 つの source(`wiki/sources/*.md`)を、Slack 等に貼れる配布用の紹介文に圧縮し、`wiki/briefs/` に 1 source = 1 ノートで保存する。source はすでに要約なので、ここでの仕事は再要約ではなく読み手向けの紹介文化である。Triggers: linked_note や `@` で source / 論文ノートを渡しつつ「この論文を紹介する文にして」「この章を紹介して」「箇条書きで要約して」「要約して」(配布・共有の意図があるとき)「Slackに貼れる形式で」「10行くらいで紹介文作って」と言ったとき、または '/wiki-brief-source', 'wiki-summarize-source', 'summarize-paper-note' と明示されたとき。単なる「このノートを説明して」や wiki 横断の「何が分かっているか」には使わない。理解を深める質問は wiki-ask-source、横断解説は wiki-query。"
---

# wiki-brief-source: source の配布用紹介文を残す

`wiki/sources/` の source ページは、すでに ingest 時点の要約である。本 skill はそれをさらに要約し直すのではなく、Slack などにそのまま貼れる紹介文へ圧縮し、`wiki/briefs/` に別ノートとして残す。目的は「論文の芯を掴んだうえで、他人に一目で伝わる形にする」ことであり、本文の切り貼りではない。規約の正本は [`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §15 である。

旧名は `summarize-paper-note` / `wiki-summarize-source`。呼び出し側が旧名を使っても本 skill を実行する。type とフォルダの識別子は `brief` / `briefs`。和文では「紹介文」「紹介文ノート」と書き、「ブリーフ」は使わない。

## 適用範囲

起動するのは、依頼が**配布・共有用の短い紹介テキスト**のときである。論文でも書籍・thesis の章でも、対象が 1 つの source なら同じ手順である。

- 「この論文を紹介する文にして」「この章を紹介して」「Slack に貼れる形式で」「10 行くらいで」
- 単一 source を示しての「要約して」(配布・共有の意図がある。wiki 全体の「何が分かっているか」は `wiki-query`)
- linked_note や `@` で source ノートが渡された状態での紹介文化

次は対象ではない。

| 質問の形 | 行き先 |
|---|---|
| このノートの内容を説明して(配布目的でない) | 通常の回答。本 skill は使わない |
| 1 source の理解を深める確認・深掘り | `wiki-ask-source` |
| 複数 source を横断する説明・比較、wiki に何があるかの要約 | `wiki-query` |
| 「〜は本当か」という命題の判定 | `wiki-thesis` |
| まだ wiki に取り込まれていない論文についての質問 | 該当する `wiki-ingest-*` を先に実行する |

## 着手前に読むもの

1. [`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §15。§2・§3(`brief:`)・§4・§5 も参照する。
2. [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md) の役割分担表。
3. `wiki/meta/japanese-style.md`。紹介文本文は常体ベースの平易な日本語。

## 手順

### 1. 対象 source を特定する

`linked_note` / `@` / ユーザー指定パスから対象を 1 つに決める。主対象は `wiki/sources/*.md`(frontmatter `type: source`)。`papers/` の論文ノートだけが渡された場合は、同じ題の `wiki/sources/@*.md` を `wiki-resolve.py` で探す。wiki source が無ければ紹介文はチャットに出し、ノートは作らずその旨を 1 行で述べて終える。

### 2. source を読んで芯を掴む

対象 source を `Read` する。frontmatter の `key_claims` は当たりをつける材料であり、それだけで済ませない。本文の「問題設定」「提案手法」「実験結果」「考察」などを読み、何が課題で、何を提案し、どう検証し、何が分かったかを自分で掴む。

出典 URL は frontmatter の `url`、なければ本文の書誌・リンクから取る。どちらも無ければ書誌行から URL を落とす。捏造しない。

### 3. 紹介文を書く

下の「紹介文の仕様」をすべて満たす本文を作る。チャット用のコードブロックと、紹介文ノートの `## 紹介文` 節は**同じ本文**にする。

### 4. 紹介文ノートを保存する

対象 source のファイル名が `wiki/sources/<BASENAME>.md` なら、紹介文ノートは `wiki/briefs/<BASENAME>.md`(**同じ basename、`@` も含めてそのまま**)。相互参照は必ずパス修飾する(`[[wiki/briefs/<BASENAME>|紹介文]]` / `[[wiki/sources/<BASENAME>|...]]`)。ベアリンク `[[<BASENAME>]]` は使わない。

**存在しない場合**: 以下の frontmatter・本文で新規作成する。作成は `python3 scripts/wiki-page-write.py "wiki/briefs/<BASENAME>.md" --content-file <一時ファイル>` の 1 呼び出しで行う。

```yaml
---
type: brief
title: "紹介: <source の title>"
date: <today> 00:00
created: <today>
updated: <today>
tags:
  - <today: YYYY/MM/DD>
  - brief
status: seed
related: []
sources:
  - "[[wiki/sources/<BASENAME>|<source の短い表示名>]]"
---

# 紹介: <source の title>

Navigation: [[index]] | [[wiki/sources/<BASENAME>|<source の短い表示名>(source)]]

対応する source ページの配布用紹介文。Slack 等へ貼るための圧縮であり、source 本文の再要約ではない。

## 紹介文

<手順 3 の本文。コードフェンスは付けない>

## 出典
- [[wiki/sources/<BASENAME>|<source の短い表示名>]]
```

**既に存在する場合**: 紹介文は常に 1 本なので、`## 紹介文` だけを最新版に差し替える。ask のように日付節を積み増さない。

1. 既存ノートを `Read` する。
2. `address`・`created`・最初の `date` を引き継ぐ。`updated` と日付タグは今日にする。`status` は既存値を残す。
3. `## 紹介文` から次の `##` の直前までを、手順 3 の本文で置き換える。`## 出典` や人間が足した他の節は消さない。
4. `## 紹介文` が無いときだけ、テンプレ本文で書き直す(その場合も `address` / `created` / 追加節は残す)。
5. 上書き用 YAML に `address: c-NNNNNN` を必ず含める。`python3 scripts/wiki-page-write.py "wiki/briefs/<BASENAME>.md" --content-file <一時ファイル> --force` で書く。address を再採番しない。

### 5. source 側から紹介文ノートへリンクする

対象 source の frontmatter に `brief:` が無ければ、`bash scripts/wiki-lock.sh acquire "wiki/sources/<BASENAME>.md"` → 末尾(`key_claims:` の後など)に `brief: "[[wiki/briefs/<BASENAME>|紹介文]]"` を追加 → `release`。既に同じ紹介文ノートを指しているなら source 側は変更しない(`updated` も動かさない)。

ロック取得や追記に失敗したら、一度だけロックを取り直して再試行する。それでも駄目なら、チャットの保存先行のあとに「紹介文ノートは書いたが source の `brief:` が未設定」と 1 行書く。紹介文ノートは消さない。

### 6. ログと索引には残さない

`wiki/log.md` と `wiki/hot.md` は更新しない。`wiki-retrieve-refresh.py` も走らせない。紹介文ノートは source から派生した配布用ノートであり、知識の一次ソースではない。`--all` は `wiki/asks/` と `wiki/briefs/` を除外する。

## 紹介文の仕様

チャットに出すコードブロックの中身と、紹介文ノートの `## 紹介文` は次をすべて満たす。

1. **箇条書き 10 行程度**。**1 行は必ず 1 文(句点 1 個)に収める**。長くなりそうなときは行を増やさず、同じ 1 文の中で削って圧縮する(従属節を削る、具体的すぎる数値や固有名詞の羅列を間引く、「〜ことで、〜する」のような二重の言い換えを一本化する)。行数の厳密さより、核心(課題 → 手法 → 検証環境 → 主要結果 → 含意)を過不足なく拾うことを優先する。
2. **文体は「少しカジュアル」かつ平易**。常体をベースに、多少くだけた言い回しは許容するが、絵文字・スラング・過度な口語(「〜だったりする」「〜って」)までは崩さない。論文調の硬い直訳や専門用語の羅列も避け、一読で意味が取れる言葉を選ぶ。
   - 硬すぎる例: 「本論文は、GPU ノードの障害のうち数値的前兆をほとんど示さないものに着目する。」
   - カジュアルすぎる例: 「GPU って急に壊れるとき、実は前兆がなかったりする。」
   - ちょうどいい例: 「GPU の一部の障害は、壊れる直前まで数値テレメトリにほぼ前兆が出ない。」
3. **意味のまとまりごとにグループ化**する。同じまとまり内の連続する文の間に空行は入れない。まとまりの境目にだけ空行を 1 行挟む。典型は「課題設定」「提案手法」「評価環境」「主要結果」「考察・含意」で、1 まとまり 2〜3 行程度が多い。
4. **最後に書誌情報を 1 行**。`---` のあと、見出しラベルは付けずに略記する。**著者の所属企業・機関を括弧書きで添える**。
   - 書式: `著者(2 人以上なら First Author et al., 単著なら full name) (所属), "タイトル," 媒体:識別子, 年. URL`
   - 所属はノート本文の「著者・所属」等から取る。First Author の所属を基本とし、企業 × 大学の共同研究など性格上重要な場合は主要な 1〜2 件まで併記する。
   - 英語論文なら `et al.` を使う。タイトルなど原文が英語の部分は和訳しない。
   - URL が取れないときは URL を省略する。推測で補わない。
5. 紹介文の本文は日本語。書誌情報だけは原語で書く。
6. key_claims の並べ替えで済ませない。下書きで 1 行に句点が 2 個以上あれば、分割せずその場で圧縮する。

## 紹介文テンプレート

```markdown
- (課題設定 1文目)
- (課題設定 2文目、あれば)

- (提案手法 1文目)
- (提案手法 2文目、あれば)

- (評価環境・データ 1文目)
- (評価環境・データ 2文目、あれば)

- (主要結果 1文目)
- (主要結果 2文目、あれば)

- (考察・含意 1文目)
- (考察・含意 2文目、あれば)

---

First Author et al. (Affiliation), "Title," Venue:ID, Year. https://...
```

グループ数や各グループの行数は論文に応じて調整してよい。大事なのは読み手が息継ぎできる塊であること。

## 既知の環境注意

`scripts/allocate-address.sh` が要求する `flock` コマンドが無いと、新規ページの address 採番が失敗する。その場合は `.vault-meta/address-counter.txt` を読み、その値を `--address c-NNNNNN --no-allocate` として `wiki-page-write.py` に渡し、成功後にカウンタファイルへ `値+1` を書き戻す。恒久対応は人間の作業として報告する。ノート保存に失敗しても、チャットの紹介文は出す。

## 出力仕様

- チャット応答: 紹介文本文だけを ` ```markdown ` コードブロックで囲む。コードブロックの直後に、保存先へのパス修飾リンクを 1 行書く。手順 5 が失敗したときだけ、その次に「source の `brief:` が未設定」を 1 行足してよい。挨拶や論文の解説は添えない。**Slack へ貼るのはコードブロックの中身だけ**。保存先と失敗報告は貼らない。
- ファイル変更: `wiki/briefs/<BASENAME>.md` の新規作成、または `## 紹介文` の差し替え 1 件。必要なら `wiki/sources/<BASENAME>.md` の frontmatter に `brief:` を追加(初回のみ)。
- `wiki/log.md` は更新しない。
