---
name: wiki-survey-recommend
description: "vault 内の蓄積から wiki-survey に渡せるテーマを推薦する。設問が未定のとき、教科書・サーベイ・文献横断調査の候補を ready / 姉妹 / 要 ingest / 別 skill に振り分ける。Triggers: 'サーベイのテーマを推薦', 'wiki-survey の候補', '何をサーベイすべき', '教科書のテーマに迷っている', 'recommend survey topics', '/wiki-survey-recommend', 'survey topic recommendation'. 設問が既にあり編纂を始めるのは wiki-survey。橋渡し文献は wiki-gap。着想は wiki-ideate。1 命題の判定は wiki-thesis。"
---

# wiki-survey-recommend: wiki-survey のテーマ推薦

`wiki-survey` は設問の逐語を受け取ってから動く。本 skill はその手前で、vault の密度と既存長編と研究関心から候補を出す。出力は推薦表と、任意で `wiki/meta/survey-recommend-YYYY-MM-DD.md` 1 枚である。長編は書かない。

**やらないこと**: `wiki-survey` の 7 フェーズ、ingest、concept / source / entity の編集、着想ノートの作成。

## 適用範囲

起動するのは、設問がまだ無く、テーマやトピックを選びたいときである。

| 設問の形 | 行き先 |
|---|---|
| 「何をサーベイすべきか」「テーマを推薦して」 | 本 skill |
| 「〜の教科書を作成して」(主題が既にある) | `wiki-survey` |
| 「このギャップに読むべき文献は」 | `wiki-gap` |
| 「この主張は本当か」 | `wiki-thesis` |
| 「このギャップから研究アイデアを」 | `wiki-ideate` |
| 「X とは何か」 | `wiki-query` |

密度だけ高くても、設問が説明や真偽なら本 skill の ready には入れない。

## 着手前に読むもの

1. [`wiki/CLAUDE.md`](../../../wiki/CLAUDE.md)、[`wiki/meta/conventions.md`](../../../wiki/meta/conventions.md)、[`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md)。`index.md`・`_index.md`・`log.md`・`hot.md` の全文は読まない。
2. [`wiki/meta/japanese-style.md`](../../../wiki/meta/japanese-style.md)。本文は常体。ダッシュ無し。
3. `wiki-survey` skill の適用範囲(設計空間、地図、対象の像)とフェーズ 1 の下限。本 skill ではスクリプトの `dense` 判定(`--min-sources`、JSON の `gates.min_sources`)がその下限を担う。本文は読まなくてよい。

---

## 手順

### 1. 機械信号だけを取る

入口は次の 1 本である。作業ディレクトリは vault ルート。

```bash
python3 scripts/wiki-survey-recommend.py
```

見てよいのは stdout の JSON だけである。`.vault-meta/clusters.json` とプロファイル本体(`profile.md`)と `wiki/index.md` を Read するな。`wiki-excerpt.py` で `sources` を数えるな(8 件で切れる)。`wiki-profile.py` と `wiki-clusters.py list` を重ねて呼ぶな。信号はスクリプトが既に畳んでいる。

| 終了 | 意味 |
|---|---|
| 0 | JSON を使う |
| 2 | 使い方の誤り。直して一度だけ再実行する |

`warnings` に `clusters_missing` があれば `python3 scripts/wiki-clusters.py build` を提案し、塊ラベル無しで続ける。`clusters_stale` は所属が古い印であり、所属は参考値にする。`profile_missing` なら関心判定を「不明」とし、対象外への切り捨てはしない。

ゲートの正本は `n_documents` である。同じ本の章は 1 文書に畳む。`n_sources` はページ数であり、下限の判定に使わない。`dense` は文書が `gates.min_sources` 以上、`near` は `gates.near_min` 以上 `gates.min_sources` 未満である。`counts.ge_min` は `--min-sources` と同じ閾値の件数である。

`survey_hits` は同一主題の既存長編(題・別名・対応/管理・監視/モニタリング)。`survey_near` は既存長編の `related` に載る近傍であり、同一教科書ではない。`cluster_id` / `cluster_label` / `on_backbone` は行に付いている。`profile_fit` は core / adjacent / out_of_scope / unknown である。

### 2. フラグを読んで箱に入れる

JSON の `dense` / `near` / `compile_debt` / `surveys` / `profile` と、各行のフラグだけを材料にする。訓練データで文献本数を足すな。

箱は排他ではない。同じ行に姉妹と負債を併記してよい。入れる順は次である。

1. **対象外** — `profile_fit` が `out_of_scope`。表に出さず、件数だけ書く。
2. **姉妹** — `survey_hits` がある行。同じ教科書をもう 1 本作らない。`survey_near` だけなら、分業できる設問があれば ready にも置ける。負債があれば「受信箱 N」を 1 語添える。
3. **要 ingest** — 関心が core / adjacent / unknown で、`dense` に入らない(`n_documents` が `gates.min_sources` 未満)。行き先は `autoresearch` / `wiki-ingest-*`。
4. **ready** — 設問の形が設計空間・地図・対象の像のいずれかで、`dense` に入り、`survey_hits` が空、対象外でない。受信箱が厚くても ready から外さない。負債は注記する。
5. **別 skill** — ready / 姉妹に載らない行のうち、真偽の 1 行なら `wiki-thesis`、受信箱だけが厚いなら `wiki-refactor` を注記する。ready を消すために使わない。

ハブが巨大なら主題を切れ。「RCA 全体」「異常検知全体」は ready にしない。塊 1 つから ready は最大 2 本(`cluster_id` を見る。`on_backbone` が false なら塊制約の外)。ready は最大 6 本、姉妹は最大 4 本、要 ingest は最大 4 本。

`survey_hits` を空のまま「エージェントが question で補う」とするな。機械が外した同一主題を ready に戻すな。

### 3. 短いリストだけ excerpt する

ready と姉妹の起点 concept に限り、`## 未解決の問い` を見る。最大 8 ページ。

```bash
python3 scripts/wiki-excerpt.py "wiki/concepts/<起点>.md" --sections "未解決の問い" --budget-tokens 800
```

地図や設計空間を求める行だけを設問の種にする。「〜は本当か」は別 skill へ送る。本文の主題節を読んでサーベイを書き始めてはならない。

### 4. 設問の逐語を 1 本付ける

ready と姉妹の各行に、`wiki-survey` の `question:` へそのまま入る文を 1 本書く。言い換え候補を並べない。骨格の出発点(教科書型 / サーベイ型 / 文献横断調査型)を 1 語で添える。判断は `wiki-survey` の `references/skeletons.md` §1 である。

### 5. 出して止まる

チャットの本文は次の順で出す。この形が出力である。

```markdown
## 信号
- concepts N、文書 <gates.min_sources の値> 以上 M、既存長編 K、テーマ塊 C

## ready
### 1. <一句の題>
- 設問: 「<逐語>」
- 起点: [[<concept>]](文書 N、ページ P、塊 <cluster_label>)
- 関心: コア / 周辺 / 不明
- 骨格: 教科書型 / サーベイ型 / 文献横断調査型
- 近傍: [[wiki/surveys/<題>]](分業) または なし
- 負債: 受信箱 N または なし

## 姉妹
### 1. <一句の題>
- 設問: 「<逐語>」(改稿または分業。新規の同一教科書は作らない)
- 既存: [[wiki/surveys/<題>]]
- 分業: <1 行>
- 負債: 受信箱 N または なし

## 要 ingest
- [[<concept>]](文書 N): <不足しているもの 1 行>

## 別 skill
- [[<concept>]]: wiki-refactor / wiki-thesis / …(<1 行>)

## 対象外
- N 件を除外した

選んだ設問の逐語を渡すまで、wiki-survey は呼ばない。
```

編纂は始めない。1 本だけ ready でも `wiki-survey` へ進まない。最後の 1 行は落とさない。ユーザーが設問の逐語を選んでから `wiki-survey` に渡す。

ユーザーが「保存するな」と言わなければ、同じ本文を `wiki/meta/survey-recommend-YYYY-MM-DD.md` に書く。当日のファイルがあれば上書きする。`wiki/{sources,entities,concepts,surveys}/` は触らない。本文を組み立て終えてから lock を取り、ただちに Write する。

```yaml
---
type: meta
title: "Survey Recommend YYYY-MM-DD"
date: YYYY-MM-DD HH:mm
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags:
  - YYYY/MM/DD
  - meta
  - survey-recommend
status: developing
related:
  - "[[index]]"
---
```

```bash
bash scripts/wiki-lock.sh acquire "wiki/meta/survey-recommend-YYYY-MM-DD.md"
# ただちに Write
bash scripts/wiki-lock.sh release "wiki/meta/survey-recommend-YYYY-MM-DD.md"

python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] wiki-survey-recommend | テーマ推薦
- Output: [[wiki/meta/survey-recommend-YYYY-MM-DD]]
- Key insight: ready N、姉妹 N、要 ingest N
EOF
)"
```

`index.md` の Surveys 節には足さない。hot にも足さない。カタログは Read と Edit を使わない。

---

## やってはいけないこと

- 設問が無い状態で `wiki-survey` を起動すること
- 出力のあとに続けて `wiki-survey` を呼ぶこと
- `wiki-excerpt.py` や訓練データで `n_sources` / `n_documents` を作ること
- ページ数(`n_sources`)で下限のゲートを判定すること
- `wiki/index.md`、`_index.md`、`log.md`、`hot.md`、`.vault-meta/clusters.json`、プロファイル本体(`profile.md`)の全文を Read すること
- `wiki-clusters.py` の id を Gap Finder の `#id` と突合すること
- テーマ塊を `wiki-survey` の精読クラスタとして使うこと
- 対象外の主題を ready に入れること
- `survey_hits` がある主題で「新しい教科書」を ready に入れること
- 受信箱が厚いことだけを理由に ready から外すこと
- 推薦を `research/ideas/` に書くこと
