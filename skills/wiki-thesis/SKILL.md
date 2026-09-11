---
name: wiki-thesis
description: "wiki の蓄積で 1 つの命題(主張)を判定する。命題を変数・予測・反証条件に分解し、支持 / 反対 / 機序 / メタ / 隣接の 5 つの角度で根拠を集め、2 周目で弱い側を狙って補い、supported / partially / contradicted / insufficient / mixed のいずれかを根拠表と反証条件つきで `wiki/questions/` に `type: thesis` として保存する。concept ページの `## 未解決の問い` を判定で閉じる手続きでもある。Triggers: 'この命題を検証して', '〜は本当か', '〜と言えるか', '〜という主張を判定して', 'thesis:', '/wiki-thesis', '未解決の問いを判定して', '〜は成り立つか', 'verify this claim against the wiki'. 単発の問いへの引用付き回答は wiki-query、設計空間や文献地図の長編編纂は wiki-survey、既存命題が引く出典の照合は claim-audit(wiki-refactor)、wiki に文献がほとんど無い段階は autoresearch / wiki-ingest-*(本 skill は insufficient で差し戻す)。"
---

# wiki-thesis: 命題の判定

`wiki-query` は問いに答え、`wiki-survey` は地図を書く。どちらにも無いのは、1 つの主張を**判定で閉じる**手続きである。concept ページの `## 未解決の問い` は ingest のたびに増えるが、落とす側の手続きが無いと受信箱が膨らむ。本 skill は 1 つの命題を受け取り、wiki の蓄積だけを根拠に、根拠表と判定と反証条件を 1 枚のページにして `wiki/questions/` に保存する。

出力は `wiki/questions/<タイトル>.md` の 1 枚(`type: thesis`)と、出自ページがあればその `## 未解決の問い` からの削除と `## 未編纂の観察` への 1 行である。

## 適用範囲

起動するのは、設問が**真偽または成立条件を問う 1 つの主張**の形をしているときである。

- 「X は Y である」「X すれば Y になる」「X は Y より速い」のような命題。
- 「〜は本当か」「〜と言えるか」「〜は成り立つか」と問われた主張。
- concept ページの `## 未解決の問い` にある 1 行(問いの形なら命題化してから扱う。フェーズ 0)。

次は対象ではない。

| 設問の形 | 行き先 |
|---|---|
| 「X とは何か」「X と Y の違いは」など、説明を求める問い | `wiki-query` |
| 「X の設計空間を体系化して」「X の文献を地図にして」 | `wiki-survey` |
| 「この concept の命題は出典どおりか」(命題と引かれた出典の照合) | `wiki-refactor` の `references/claim-audit.md` |
| wiki に該当文献がほとんど無い主題 | `autoresearch` / `wiki-ingest-*`(本 skill はフェーズ 1 のゲートで insufficient を出して差し戻す) |
| 「X と Y は矛盾するか」だけを聞く問い | `contradiction-index.py --query`(wiki-query の経路) |

判定は「命題が真か」ではなく「**wiki のソースは命題をどこまで支持するか**」である。wiki に無い知識で判定を埋めない。訓練データから支持も反対も持ち込まない。

## 着手前に読むもの

1. [`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §2・§3(`thesis` の frontmatter)、§5(出典と矛盾)、§8(`## 未解決の問い` の落とし方)。
2. [`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md)。`index.md`・`_index.md`・`log.md`・`hot.md` の全文は読まない。
3. [`wiki/meta/japanese-style.md`](../../../wiki/meta/japanese-style.md) と `japanese-tech-writing` skill。本文は常体、ダッシュ無し。
4. `wiki-query` skill の検索経路(`retrieve.py` から `wiki-excerpt.py`)。本 skill は同じ経路を使う。
5. 5 つの角度の定義と subagent へ渡すブリーフは [`references/pass-brief.md`](references/pass-brief.md) が正本である。

---

## 手順

### フェーズ 0　命題の確定と分解

1. **命題を逐語で保存する**。frontmatter の `thesis` にそのまま入れる。問いの形(「〜か」)で渡されたら、答えが「はい」になる形に直した命題を `thesis` に置き、元の問いは `question` に残す。
2. **出自を記録する**。concept ページの `## 未解決の問い` から起こしたなら、そのページを `origin` に入れ、元の行の一部文字列を控える(フェーズ 6 で落とすときに `--remove-question-containing` に渡す)。
3. **分解する**。次の 4 つを本文の「命題の分解」節に書く。ここが曖昧なままだと、支持と反対が別の命題を相手にする。
   - **変数**: 主語(何について)、述語(何が成り立つ)、範囲(どの条件・規模・時期で)。範囲が命題に無ければ「範囲: 無指定」と書き、判定で partially になりうることを予告する。
   - **予測**: 命題が真なら wiki のソースに**何が書かれているはずか**(実測値、比較結果、設計判断)。
   - **反証条件**: どんな記述が 1 件あれば命題が崩れるか。判定の後で「反証条件」節に再掲し、将来の ingest が当たったら本ページを見直せる形にする。
   - **隣接命題**: 似ているが別の命題(強い版、弱い版、逆向き)。検索で拾った根拠が隣接命題のものなら、本命題の根拠に数えない。
4. 複合命題(「A であり、かつ B である」)は分ける。1 ページ 1 命題。分けた片方は別ページにするか、本ページの「隣接命題」に置いて判定の対象外と明記する。

### フェーズ 1　根拠の収集(コーパス先行)

wiki の外へ出ない。web 検索も訓練データも使わない。

```bash
# 1. 命題そのものと、否定形・条件を変えた言い換えで retrieve を叩く(グラフ路が近傍を補う)
python3 scripts/retrieve.py "<命題の逐語>" --top 10
python3 scripts/retrieve.py "<命題の否定形または対立する見解>" --top 10
python3 scripts/retrieve.py "<機序を問う形: なぜ X なら Y か>" --top 8

# 2. 既存の矛盾 callout に当たっているか
python3 scripts/contradiction-index.py --query <主語の語> <述語の語> --limit 10

# 3. 命題に触れる concept が既に命題を持っているなら、その命題と出典の対を取る
python3 scripts/claim-audit.py sample --pages "wiki/concepts/<concept>.md" --per-page 30 --reaudit > /tmp/thesis-claims.json

# 4. 名前が出た entity と source の実在確認
python3 scripts/wiki-resolve.py "<名前>" --type any
```

- `retrieve.py` の候補のうち `channels: ["graph"]` だけのものは、BM25 が拾えない語彙のページである。命題に触れているかは抜粋で確かめる。
- 候補ページは `wiki-excerpt.py <path> --tail 15 --budget-tokens 1800` で読む。全文 Read しない。抜粋で足りないときだけ `--section` で節を指定して広げる。
- 候補の和集合から、**命題に触れている source ページ**を数える。concept ページの命題は source を経由して数える(concept の命題 1 行は、その `根拠:` が引く source の本数だけ独立した根拠になる。concept 自体を 1 本と数えない)。

**ゲート**。命題に触れている source が **2 本未満**なら、フェーズ 2 に進まず `insufficient` で判定を書く(フェーズ 4 へ)。差し戻し節に、足りない側(支持か反対か)と、`autoresearch` または `wiki-ingest-paper` に渡す検索語を具体的に書く。web で補わない。

### フェーズ 2　5 つの角度で読む

角度の定義は [`references/pass-brief.md`](references/pass-brief.md) §1 に従う。

| 角度 | 問うこと |
|---|---|
| **支持** | 命題の予測どおりの記述はあるか。実測か、主張か、伝聞か |
| **反対** | 予測と食い違う記述、反証条件に当たる記述はあるか。矛盾 callout は既にあるか |
| **機序** | なぜそうなるかの説明はあるか。支持と反対の食い違いを説明する条件変数はあるか |
| **メタ** | 根拠の質。評価条件(規模、版、データ出自)、著者の利害、単一グループへの偏り、伝聞の連鎖 |
| **隣接** | 拾った根拠が隣接命題(強い版、弱い版、逆向き)のものではないか。命題の範囲を狭めれば成り立つか |

進め方は候補の規模で決める。

- 候補ページが **8 枚以下**なら、メイン文脈が 5 つの角度を順に当てる。1 ページを読むたびに 5 角度すべてを問う(角度ごとに読み直さない)。
- **9 枚以上**なら、角度ごとに Explore subagent を 1 体、計 5 体を並列投入する。ブリーフは [`references/pass-brief.md`](references/pass-brief.md) §2 の雛形を使う。**「担当ページに書かれていないことは推測せず『記載なし』と明記する」を必ず入れる**。subagent にファイルを書かせない。結果はテキストで返させ、Write はフェーズ 5 でメイン文脈が 1 回だけ行う。

根拠は 1 件ごとに次の形で控える(フェーズ 5 の根拠表の行になる)。

```
| 側 | 根拠の要約(数値は評価条件つき) | 出典 [[@...]] と節 | 強さ | 備考 |
```

**強さ**は 3 段階: **直接**(その source が自ら測った・観察した)、**間接**(その source が主張しているが測定は別の設定、または別条件からの外挿)、**伝聞**(その source が他の文献を引いているだけ)。伝聞は原典が wiki にあれば原典に差し替え、無ければ伝聞のまま数える。

### フェーズ 3　2 周目: 弱い側を狙う

1 周目の根拠表で、支持と反対のどちらが薄いかを見る。**薄い側だけを対象に**もう 1 周する。

- 支持が厚いなら、反対を探す: 命題の主語を別の規模・別の版・別のワークロードに置いた言い換えで `retrieve.py` を叩く。`contradiction-index.py --one-sided` の候補に主語が出ていないか見る。
- 反対が厚いなら、支持を探す: 命題を支持しそうな設計判断を持つ system の entity ページ(`wiki-resolve.py --type entity`)から source を辿る。
- 2 周目でも薄い側が **0 件**なら、それ自体を根拠表の備考に書く(「反対する記述は wiki のソースからは見つからなかった。探索語: …」)。探していないことと見つからなかったことを区別する。

2 周目で追加した行は根拠表で「2 周目」と印を付ける。1 周目で判定を決めてから 2 周目で補強する順番にしない。

### フェーズ 4　判定

根拠表から判定を 1 つ選ぶ。基準は次のとおりで、上から順に当てる。

| 判定 | 基準 |
|---|---|
| **contradicted** | 反証条件に当たる**直接**の根拠が 1 件以上あり、それを上回る強さの支持が無い。または既存の矛盾 callout が未決着のまま命題の側を否定している |
| **mixed** | 支持と反対の双方に**直接**の根拠があり、評価条件の違い(規模、版、ワークロード、データ出自)で説明できる条件変数が見つからない |
| **partially** | 命題の範囲を狭めれば(特定の規模・版・条件で)支持が揃うが、無指定の範囲では反対または記載なしが残る。狭めた命題を「判定」節に書き直す |
| **supported** | 独立した **2 本以上**の source が直接または間接に支持し、同等以上の強さの反対が無く、機序の説明と矛盾しない |
| **insufficient** | 命題に触れる source が 2 本未満、または全部が伝聞。フェーズ 1 のゲートで出る場合と、フェーズ 3 の後でも根拠が揃わない場合の両方 |

- **独立**の意味: 著者グループが異なる、または同一グループでも測定対象が異なる。同一論文の章分割 source は 1 本と数える。
- `confidence` は `high` / `medium` / `low`。直接の根拠が 3 本以上で high、直接 1 本から 2 本で medium、間接だけなら low。insufficient は常に low。
- 判定文は「wiki のソースは〜を支持する / 支持しない」の形で書く。「〜は真である」と書かない(判定は wiki の範囲に対するものである)。
- **反証条件**を判定の直後に再掲する。将来の ingest がこの条件に当たる source を入れたとき、本ページを見直す合図になる。

### フェーズ 5　保存

本文を組み立て終えてから、1 回の呼び出しで書く。`wiki-page-write.py` が採番・検証・ロック・書き込みを行う。

```bash
python3 scripts/wiki-page-write.py "wiki/questions/<タイトル>.md" --content-file /tmp/thesis.md
```

frontmatter は conventions §2 の標準に `thesis` 固有フィールド(§3)を足す。

```yaml
---
type: thesis
title: "<命題を短くした名詞句または問い>"
thesis: "<命題の逐語>"
question: "<元の問いの逐語。問いから起こしたときだけ>"
verdict: supported          # supported|partially|contradicted|insufficient|mixed
confidence: medium          # high|medium|low
origin: "[[<出自の concept>]]" # 未解決の問いから起こしたときだけ
judged: YYYY-MM-DD
date: YYYY-MM-DD HH:mm
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: developing
tags:
  - YYYY/MM/DD
  - thesis
  - <domain-tag>
related:
  - "[[<触れた concept / entity>]]"
sources:
  - "[[@<根拠表に出た source>]]"
---
```

本文の節は固定する。

```markdown
# <title>

> [!abstract] 判定: **<verdict>**(confidence: <c>)
> wiki のソースは <命題> を <どこまで支持するか 1 文>。

## 命題の分解
- 変数: 主語 / 述語 / 範囲
- 予測: …
- 反証条件: …
- 隣接命題: …

## 根拠表
| 側 | 根拠の要約 | 出典 | 強さ | 備考 |
|---|---|---|---|---|
(支持、反対、機序、メタ、隣接の順。2 周目の行は備考に「2 周目」)

## 機序
(なぜそうなるかの説明と、支持と反対を分ける条件変数。無ければ「wiki のソースからは確認できない」)

## 判定
(基準のどれに当たったか。partially なら狭めた命題を書き直す。mixed なら未解決の条件変数を書く)

## 反証条件
(判定を覆す記述の形。将来の ingest への合図)

## 差し戻し
(insufficient / mixed のとき。足りない側と、autoresearch / wiki-ingest-paper に渡す検索語。それ以外は「無し」)

## 関連
- 概念: [[...]]
- エンティティ: [[...]]

## 出典
- [[@...]] (何を根拠にしたか)
```

- 数値は評価条件を添える。異なる設定の数値を同じ行で比べない。
- 出典は全て `@` 付き(conventions §4)。`papers/` を意図的に指すときだけパス修飾する。
- 保存後に検査する。wikilink の実在(wiki-survey skill フェーズ 6 の検査 1 と同じ手順)、数値の遡及(根拠表の数値を出典ページに `rg` で確かめる)、整文(ダッシュ 0 件、wikilink を除いた中黒 0 件。数値や条件の並列「387 回・0.895 ms」は読点にする)。直しは `wiki-lock.sh acquire` と `release` で包む。
- contradicted または mixed のとき、対立する source ページ同士に `> [!contradiction]` callout が無ければ、フェーズ 6 で両ページに足す(conventions §5)。

### フェーズ 6　出自ページへの反映

`origin` があるときだけ行う。concept の主題節は書き換えない(conventions §8。主題節へ畳むのは `wiki-refactor` の再編纂の仕事で、`recompile-queue.py` が拾う)。

```bash
python3 scripts/wiki-append.py --page "wiki/concepts/<origin>.md" \
  --remove-question-containing "<元の行の一部文字列>" \
  --observation "[<主題節名>] <命題> は wiki のソース <N> 本で <verdict>(判定 [[<thesis ページ>]])。<1 文の要点>。(Source: [[@...]], [[@...]])" \
  --related-concept "[[<thesis ページ>]]"
```

- 判定が **insufficient** なら問いは落とさない。代わりに問いの末尾へ `(判定 [[<thesis ページ>]]: insufficient、不足は …)` を足す(`--remove-question-containing` と `--question` で置き換える)。
- 判定が **insufficient** か **mixed** で、不足が「wiki に文献が無い」ではなく「誰も測っていない」「対立を分ける変数を誰も切り分けていない」の形なら、`wiki-ideate` へ渡せる着想の種である。ユーザーに 1 行で告げる(本 skill は着想を書かない)。
- 判定が **contradicted** / **mixed** で矛盾 callout が無いなら、対立する両ページに callout を足す。これは通常の wiki ページ書き込みなので `wiki-lock.sh acquire` と `release` で包み、1 ページ = 1 コミットにする。
- `wiki-append.py` は自己ロックする。`wiki-lock.sh` で包まない。

### フェーズ 7　カタログ更新

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] wiki-thesis | <title>
- Thesis: 「<命題の逐語>」
- Verdict: <verdict>(confidence <c>)。支持 <n> 件 / 反対 <m> 件 / source <k> 本
- Output: [[wiki/questions/<title>]](address c-NNNNNN)
- Origin: [[<concept>]] の未解決の問いから(該当時)。問いを落とし、未編纂の観察へ 1 行
- 2 周目: 狙った側と、追加できた件数
- Key insight: 複数ソースを並べて初めて言えた観察
- Gap: 反証条件に当たる source が wiki に無いなど
EOF
)"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/index.md --section "Questions" \
  --line "- [[wiki/questions/<title>]]: <verdict>。<1 行要約>"

python3 scripts/wiki-catalog.py prepend-hot --text "## YYYY-MM-DD | wiki-thesis | <title>
- [[wiki/questions/<title>]]
- Focus: <命題>
- Key insight: <判定と決め手>
"

python3 scripts/wiki-retrieve-refresh.py --pages "wiki/questions/<title>.md" "wiki/concepts/<origin>.md" --no-llm
```

コミットは `wiki: judge <title>`。出自ページの変更と矛盾 callout は別コミットにする。

---

## やってはいけないこと

- **wiki に無い知識で支持や反対を埋める**。訓練データ由来の根拠は出典に遡及できず、判定そのものを無効にする。無いものは「wiki のソースからは確認できない」と書き、差し戻し節に検索語を残す。
- 1 周目で判定を決めてから 2 周目を補強に使うこと。2 周目は**弱い側**のためにある。
- 隣接命題の根拠を本命題の根拠に数えること。範囲が違えば別の命題である。
- concept の主題節を直接書き換えること。落とすのは `## 未解決の問い` の行、足すのは `## 未編纂の観察` の 1 行だけ。
- 「〜は真である」と書くこと。判定は wiki の範囲に対する支持の度合いである。
- `wiki/index.md`・`_index.md`・`wiki/log.md`・`wiki/hot.md` の全文読み込み。カタログを Read と Edit で更新すること。
- 既存の `papers/`・`research/`・`structures/`・`notes/` の書き換え。
- subagent にファイルを書かせること。
- insufficient を web 検索で埋めること。文献を足す工程は `autoresearch` と `wiki-ingest-*` の仕事である。
