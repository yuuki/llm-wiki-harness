---
name: wiki-ideate
description: "wiki のギャップ(wiki-gap の real-gap)や判定で閉じなかった命題(wiki-thesis の insufficient / mixed)、concept の `## 未解決の問い` を入力に、研究の着想を 1 本起こし、wiki(retrieve)と arXiv / DBLP で新規性を照合してから、人間の承認を得て `research/ideas/` に新規ノートを 1 枚作る。実験の実行や論文の執筆は行わない。Triggers: '着想を出して', 'このギャップから研究アイデアを', 'この問いから何が作れるか', '新規性を確認して', 'ideate:', '/wiki-ideate', 'research idea from this gap', 'is this idea novel'. 文献推薦で止まるのは wiki-gap、命題の判定は wiki-thesis、問いへの回答は wiki-query、web から集める段階は autoresearch。"
---

# wiki-ideate: ギャップから着想を起こし、新規性を照合する

`wiki-gap` は文献推薦で止まり、`wiki-thesis` は判定で止まる。どちらも「ここに研究の余地がある」と言った先が無い。本 skill はその継ぎ目を担う。入力はギャップか閉じなかった命題か未解決の問いの 1 つ、出力は着想ノート 1 枚(既定は `research/ideas/<題>.md`)と、出自の wiki ページへの 1 行である。`research/ideas/` が無い vault では、承認後も `/tmp` に置いて場所を報告する。

**やらないこと**: 実験計画の実行、コードの生成、論文の下書き、複数の着想の量産。1 回の起動で 1 本に絞る。着想の候補を 3 本まで並べるのは選ぶためであり、書き残すのは選んだ 1 本だけである。

## 適用範囲

起動するのは次のいずれかを入力に渡されたときである。

| 入力 | 出所 | 読むもの |
|---|---|---|
| 実在ギャップ(real-gap) | `wiki/meta/gap-report-YYYY-MM-DD.md` の 1 行(ユーザーが指す) | その行の両側の concept と共通出典 |
| 閉じなかった命題 | `wiki/questions/<thesis>.md` の `verdict: insufficient` か `mixed` | 根拠表、機序、反証条件、差し戻し |
| 未解決の問い | concept ページ `## 未解決の問い` の 1 行 | その concept の定義と主題節の outline、問いの周辺の観察 |
| 自由な種 | 「A と B の間に何か作れないか」 | A と B の concept ページ |

次は対象ではない。

| 設問の形 | 行き先 |
|---|---|
| 「このギャップに読むべき文献は」 | `wiki-gap` |
| 「この主張は本当か」 | `wiki-thesis` |
| 「X とは何か」 | `wiki-query` |
| 「この着想を実装して」「実験を回して」 | 本 skill の外(ユーザーの研究プロジェクト) |
| 「この着想を論文にして」 | 本 skill の外 |

着想は**研究関心の中**に置く。フェーズ 0 で `wiki-profile.py` の要約に照らし、プロファイルの対象外に落ちる着想は、そう告げて止める。終了 3(プロファイルが無い)なら関心判定を飛ばす。

## 着手前に読むもの

1. [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md)、[`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md) §5(出典)、[`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md)。`index.md`・`_index.md`・`log.md`・`hot.md` の全文は読まない。
2. [`wiki/meta/japanese-style.md`](../../../wiki/meta/japanese-style.md) と `japanese-tech-writing` skill。本文は常体、ダッシュ無し。
3. [`.claude/skills/bibliography-lookup.md`](../bibliography-lookup.md)。外側照合は arXiv が先。DBLP は JSON が返ったときだけ。**検索語は concept 名と論文題名だけ**。文や着想の説明文を検索語にしない。Semantic Scholar Graph API は使わない。
4. `research/ideas/` がある vault では、そこは人間所有の一次レイヤーである。**既存ノートは読むだけで書き換えない**。新規ファイルを 1 枚足すだけ。ディレクトリが無ければ姉妹検索も保存も行わず、承認後は `/tmp` に置く。

---

## 手順

### フェーズ 0　入力の確定と関心の照合

- 入力の種類(上表)と出自ページを確定し、出自の逐語(ギャップ行、命題、問いの行)を控える。ノートの `origin` にそのまま入れる。
- `python3 scripts/wiki-profile.py` を読み、入力がコア関心か周辺関心か対象外かを 1 行で判定する。対象外なら理由を告げて止める。周辺関心なら、プロファイルが示すコア軸へどう接続するかを着想の条件にする。終了 3 ならこの判定を飛ばす。
- 姉妹の着想を先に探す。`research/ideas/` があるときだけ `rg` で当たる(wiki の索引には無い)。

```bash
rg -il "<キーワード1>|<キーワード2>" research/ideas/ | head
```

ヒットしたノートは `wiki-excerpt.py` ではなく `Read` で先頭 30 行を読む(一次レイヤーは excerpt の対象外)。ほぼ同じ着想があれば、新規ノートを作らず、そのノートへの追記をユーザーに提案して止める(追記自体は人間が行う)。

### フェーズ 1　根拠の収集

wiki が入力の周辺で何を知り、何を知らないかを確定する。ここで集めた根拠がノートの「前提となる wiki の根拠」になる。

```bash
python3 scripts/retrieve.py "<入力の主題>" --top 10
python3 scripts/wiki-excerpt.py "wiki/concepts/<A>.md" --outline
python3 scripts/wiki-excerpt.py "wiki/concepts/<A>.md" --section "未解決の問い" --budget-tokens 800
python3 scripts/wiki-excerpt.py "wiki/questions/<thesis>.md" --section "差し戻し" --budget-tokens 600   # thesis 入力のとき
python3 scripts/contradiction-index.py --query "<主題>"                                              # 矛盾があれば着想の種
```

集めるもの(各 1〜3 行):
- **wiki が知っていること**: 関係する concept の命題(太字行)と、それを支える source。
- **wiki が知らないこと**: ギャップ行そのもの、thesis の「差し戻し」に書かれた不足、未解決の問い。
- **矛盾**: 対立する source があれば、それを分ける変数(wiki-thesis がやったように)。着想は矛盾を分ける変数の上に立つことが多い。
- **手持ちの道具**: wiki にある手法・データセット・ベンチマークの entity(`wiki-resolve.py --type entity`)。着想の検証に使えるものを控える。

### フェーズ 2　着想の候補と選択

候補を **3 本まで** 1 行ずつ書く。各行は「<対象> に <手法・視点> を持ち込み、<ギャップ> を埋める」の形にする。次を満たさない候補は落とす。

- wiki の根拠(フェーズ 1)に 1 つ以上つながる。
- 検証の最小設計が 1 段落で書ける(何を測れば着想の当否が分かるか)。
- 研究関心のコア・周辺に入る(フェーズ 0)。

ユーザーが 1 本を選ぶ。返答が無い自律実行では、根拠のつながりが最も多い 1 本を選び、選ばなかった候補はノートの末尾に 1 行ずつ残す。

### フェーズ 3　着想の下書き

選んだ 1 本を次の形で 15〜30 行に書く(ノート本文になる)。

```markdown
## 問題
(誰が、どの状況で、何に困るか。wiki の根拠を (Source: [[...]]) で添える)

## 着想
(1 段落。対象、持ち込む手法・視点、なぜそれで埋まるか)

## 仮説
- H1: ...(反証できる形。「〜なら〜が〜%以上〜する」)

## 検証の最小設計
(データ、比較対象、指標、規模。wiki にある entity を道具に使う)

## 期待される差分
(既存手法に対して何が変わるか。数値目標があるなら評価条件つき)

## 前提となる wiki の根拠
- [[concept]]: 命題の要約 (Source: [[@...]])

## リスク
- (成立しない場合の最有力の理由)
```

- 数値は評価条件を添える。訓練データからの数値を根拠にしない。
- 着想の説明に「革新的」「画期的」の類を使わない。差分は具体で書く。

### フェーズ 4　新規性の照合

内側(wiki と `research/ideas/`)と外側(arXiv。DBLP は JSON が返るとき)の両方で照合する。**判定は「同じことをやった仕事があるか」であり、「似た分野の仕事があるか」ではない。** 呼び出しは [bibliography-lookup.md](../bibliography-lookup.md) に従う。Semantic Scholar Graph API は使わない。

内側:

```bash
python3 scripts/retrieve.py "<着想の鍵語(concept 名の組み合わせ)>" --top 10
rg -il "<鍵語>" research/ideas/ wiki/sources/ | head
```

外側(検索語は concept 名か論文題名の形に限る。2〜4 回。arXiv を先に。`urlencode` は bibliography-lookup.md の関数):

```bash
curl -sS -m 30 "https://export.arxiv.org/api/query?search_query=all:$(urlencode "<concept 名 A> <concept 名 B>")&max_results=10"
# DBLP は JSON が返ったときだけ使う。HTML(ボット判定)なら捨てて再試行しない。
# 近い 1 本に arXiv ID があれば id_list で要旨と年を取る。DOI なら Crossref。
```

近い仕事を **3 本まで**、次の 3 段階で判定する。

| 判定 | 意味 | 扱い |
|---|---|---|
| same | 対象・手法・問いが一致する | 着想を捨てるか、その仕事との差分に作り直す(フェーズ 3 へ戻る。戻りは 1 回まで) |
| partial | 対象か手法の一方が一致する | ノートの「近い仕事」に差分つきで残す |
| different | 分野が近いだけ | 書かない |

新規性の総合判定は **novel / incremental / already-done** の 3 値。already-done は same が 1 本でもあるとき、incremental は partial が 2 本以上で差分が実装上の違いに留まるとき、それ以外が novel。判定の根拠は「近い仕事」の各行に 1 文で書く。

近い仕事が wiki に無い論文なら、`wiki-ingest-paper` への引き渡し候補としてノートに書く(ここでは取り込まない。取り込みは wiki-gap と同じくユーザーの指示で)。

### フェーズ 5　承認と保存

ユーザーに、着想(フェーズ 3)と新規性判定(フェーズ 4)と保存先ファイル名を示し、**承認を得てから書く**。自律実行で承認が取れないとき、または `research/ideas/` が無いときは、ノートを `/tmp/idea.md` に置いて場所を報告し、一次レイヤーには書かない。

承認後、`research/ideas/` があれば `research/ideas/<題>.md` を新規作成する。題は日本語の名詞句。**同名のファイルがあれば書かず、別の題を提案する。** 既存ノートの形式に合わせた frontmatter に、出自と判定を足す。

```yaml
---
date: YYYY-MM-DD HH:mm
aliases: []
tags: [YYYY/MM/DD, idea]
url:
origin: "[[<出自ページ>]]"          # gap-report / thesis / concept
origin_kind: gap | thesis | question | seed
novelty: novel | incremental | already-done
closest:
  - "<近い仕事の題名> (<年>, <判定>)"
---
```

本文はフェーズ 3 の形に、`## 近い仕事`(フェーズ 4 の表。wiki にあるものは `[[@...]]`、無いものは題名と arXiv ID か DOI)と、選ばなかった候補があれば `## 選ばなかった候補` を続ける。wiki のページへの wikilink は名前だけ(`[[根本原因分析]]`)で書く(Obsidian が解決する)。

書き込みは通常の `Write`(一次レイヤーなので `wiki-page-write.py` は使わない。address も採番しない)。書いた後に `rg -c '——|―' research/ideas/<題>.md` でダッシュ 0 件を確かめる。

### フェーズ 6　出自ページへの 1 行

wiki から着想ノートへは**一方向**でつなぐ。出自の種類ごとに 1 行だけ。

- **concept の未解決の問い**: 問いは落とさない(着想は答えではない)。`python3 scripts/wiki-append.py --page "wiki/concepts/<A>.md" --observation "[着想] <問い> から着想 [[<題>]] を起こした(新規性 <判定>、近い仕事 <N> 本)。"`
- **thesis**: 出自の concept があればそこへ同じ 1 行。thesis ページ自体は書き換えない(判定ページは固定節)。
- **gap-report**: 何も書かない(report は meta であり編集しない)。ギャップの両側の concept のうち、着想が主に育てる側に上の 1 行を足す。
- **自由な種**: 関係する concept 1 つに同じ 1 行。

`research/ideas/` の**既存ノート**には何も書かない。

### フェーズ 7　カタログ更新とコミット

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] wiki-ideate | <題>
- Origin: [[<出自ページ>]](<gap|thesis|question|seed>)。逐語: 「<ギャップ行・命題・問い>」
- Idea: <1 文>
- Novelty: <novel|incremental|already-done>。近い仕事: <題名 (年, 判定)> ×N。wiki に無い近い仕事: <題名>(wiki-ingest-paper 候補)
- Output: research/ideas/<題>.md(承認済み / 未承認なら /tmp に置いた旨)
- Key insight: <着想を成り立たせる wiki 側の根拠 1 文>
EOF
)"
```

コミットは `research: add idea <題>`(着想ノート)と `wiki: link idea <題> from <concept>`(出自への 1 行と log)の 2 つに分ける。一次レイヤーと wiki レイヤーを同じコミットに混ぜない。

---

## やってはいけないこと

- `research/ideas/` の既存ノートを書き換える(読むだけ)。`papers/` `research/` `structures/` `notes/` は温存(CLAUDE.md 鉄則)。
- 承認なしに `research/ideas/` へ書く。自律実行では `/tmp` に置いて報告する。
- 1 回の起動で 2 本以上の着想ノートを作る。
- 書誌 API に文や説明を投げる。検索語は concept 名と論文題名だけ。Semantic Scholar Graph API は使わない。
- 訓練データの知識で新規性を「無い」と断じる。判定は wiki と arXiv(および使えた DBLP / Crossref)の結果に基づき、根拠の無い判定は書かない。
- 実験の実行、コードの生成、論文の節の執筆。着想の先はユーザーの研究プロジェクトの仕事である。
- `wiki/index.md`・`_index.md`・`log.md`・`hot.md` の全文 Read。
