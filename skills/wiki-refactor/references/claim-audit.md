# 命題の再検証(claim audit)の手順

`wiki-refactor` skill の従属資料。concept ページの主題節にある太字命題を標本抽出し、命題が引く出典に**本当にその記述があるか**を照合する。再編纂は観察を命題へ畳む工程で一般化・数値のずれ・wiki 側の解釈の混入を起こしうるが、それを検査する工程が無かった。本手順は nvk/llm-wiki の adversary エージェントと lacuna の sweep を、専用プログラムではなくスクリプト 1 本と判定 JSON に落としたものである。

判定は「命題が真か」ではなく「引かれた出典がその命題を支持するか」である。命題が正しくても出典が違えば `not_in_source` になる。

---

## 0. 前提

読む: `CLAUDE.md`、`wiki/CLAUDE.md`、`wiki/meta/conventions.md` §5・§8、`wiki/meta/token-discipline.md`、この文書。

読まない: `wiki/index.md`、`wiki/hot.md`、`wiki/log.md`、各 `_index.md`。対象 concept ページも全文 Read しない。パケットの抜粋で足りないときだけ source ページを `wiki-excerpt.py` で見る。

スクリプトは wiki の本文ページを書かない。書くのは台帳 `.vault-meta/claim-audit.json`(git 追跡、`-f` で add)と報告書 `wiki/meta/claim-audit-YYYY-MM-DD.md` だけである。命題への手当て(§4)は本手順の外で、wiki-refactor の通常規則(lock、1 ページ = 1 コミット)に従って行う。

---

## 1. 標本抽出

```bash
# 再編纂直後の自己検査(対象ページの命題を全部)
python3 scripts/claim-audit.py sample --pages "wiki/concepts/<page>.md" --per-page 20 > /tmp/claim-sample.json

# 定期検査(lint から。直近に再編纂されたページを新しい順に 5 頁 × 2 命題)
python3 scripts/claim-audit.py sample --recompiled --limit 5 --per-page 2 --seed "$(date +%Y%m%d)" > /tmp/claim-sample.json

# 抜き打ち(concept をランダムに 10 頁 × 1 命題)
python3 scripts/claim-audit.py sample --random 10 --per-page 1 --seed "$(date +%Y%m%d)" > /tmp/claim-sample.json
```

出力の `claims[]` は 1 件が「命題 + 根拠行 + 出典ごとの根拠候補」である。

- `claim`: 太字命題の本文。`section`・`line` は所在。
- `sources`: 命題行と `- 根拠:` / `- 反証:` 行が引く source ページ名。`- 関連:` は含めない。
- `evidence[]`: 出典ごとに `status`(ok / stub / source_missing / unreadable)、`page_excerpts`(source ページで命題と語彙が重なる段落・箇条書き上位 3 件)、`raw_excerpts`(`.raw/` の抽出テキストがあれば同様に上位 3 件)。

台帳に判定済みの命題は既定で除かれる。同じ命題を再判定するときだけ `--reaudit`。

---

## 2. 判定

命題 1 件ごとに、`evidence` を読んで次のいずれかを付ける。

| 判定 | 意味 | 典型 |
|---|---|---|
| `supported` | 出典の記述が命題を支持する | 数値・条件・向きが一致 |
| `unsupported` | 出典の記述が命題と食い違う | 数値が違う、条件が落ちて一般化されている、因果の向きが逆 |
| `not_in_source` | 出典にその内容が無い | 2 出典の各半分は書いてあるが「A は B を圧縮したものだ」という対応づけ自体はどちらにも無い(wiki 側の解釈を事実の形で書いている) |
| `source_missing` | source ページが存在しない | 改名漏れ、未 ingest |
| `unclear` | 抜粋では決められない | source ページを `wiki-excerpt.py` で見て再判定し、それでも決まらなければ `unclear` のまま残す |

規則:

- 抜粋で決められないときは、まず `python3 scripts/wiki-excerpt.py "<source page>" --budget-tokens 1500` で source ページを見る。`.raw/` の PDF を開きに行かない(抽出テキストがあればパケットに入っている)。
- `evidence[].status` が `stub` の出典は wiki からは検証できない。その出典だけが根拠なら `unclear`、他の出典で支持されるなら `supported` とし、note に「stub 未検証」と書く。
- 命題が複数出典を束ねる場合、**全部の出典で各部分を確認**してから `supported` にする。半分しか見ていないなら `unclear`。
- `note` は一文。判定の根拠(どの抜粋のどの記述)か、`not_in_source` なら何が出典に無いのかを書く。次の担当者が同じ判断に到達できる粒度にする。
- 命題そのものの真偽で判定しない。世間で正しいと知っていても、出典に無ければ `not_in_source`。

判定は JSON に書く。`id`・`page`・`line`・`claim`・`sources` はパケットからそのまま写す。

```json
[
  {"id": "f0b16e3c72", "page": "wiki/concepts/サービスレベル目標.md", "line": 363,
   "claim": "…", "sources": ["@2022__…", "@2019__…"],
   "verdict": "not_in_source",
   "note": "両出典は各半分を個別に述べるだけで、『圧縮したもの』という対応づけはどちらにも無い"}
]
```

---

## 3. 記録

```bash
python3 scripts/claim-audit.py record --verdicts /tmp/claim-verdicts.json --by agent   # 人間が判定したら --by human
python3 scripts/claim-audit.py report                                                  # 累計と問題率
```

`record` は台帳に追記し(同じ id の再判定は `history` に前回を残す)、`wiki/meta/claim-audit-YYYY-MM-DD.md` を当日分から再描画する。報告書は手で編集しない。

---

## 4. 手当て(supported 以外)

命題を黙って直さない。conventions §8 更新ルール 4 のとおり、痕跡を残して直す。

| 判定 | 手当て |
|---|---|
| `not_in_source` / `unsupported` | 命題の根拠入れ子に `- 留保: 再検証 YYYY-MM-DD — <出典に無い/食い違う点を一文>` を足す。命題文を弱める(「X は Y を圧縮したものである」→「X と Y は同じ順序の逆転を述べる」)のは再編纂の一部として行い、元の文は退避 callout に残す。出典の記述と**正面から食い違う**なら `> [!contradiction]` callout を命題の直下と source ページの両方に置く(§5)。 |
| `source_missing` | `python3 scripts/wiki-resolve.py "<name>" --type source` で改名先を探し、wikilink を直す。無ければ lint の dead link として扱う。 |
| `unclear` | 何もしない。次回の `--reaudit` で再判定する。 |

手当ての書き込みは `bash scripts/wiki-lock.sh acquire <path>` / `release <path>` で囲み、1 ページ = 1 コミット(`wiki: add 留保 to <page> after claim audit`)。手当てを終えたら台帳に記す。

```bash
python3 scripts/claim-audit.py resolve <id> --note "留保を付けた"
```

---

## 5. 報告

呼び出し元へ返すもの(ファイルには書かない):

- 標本数と判定の内訳(`report` の出力)
- `supported` 以外の命題の一覧と手当ての状況(済 / 未)
- 出典側の問題(stub が根拠になっている、source ページが薄くて検証できない)
- 手順側の不備(パケットの抜粋が命題と噛み合わなかった等)

---

## 6. やらないこと

- 命題を出典の確認なしに `supported` にしない。
- 判定のために `.raw/` の PDF を開かない。抽出テキストで足りなければ `unclear`。
- 台帳と報告書を手で編集しない。
- 手当てを本手順の中で複数ページまとめてコミットしない。
- source ページを書き換えない(contradiction callout を置く場合を除く)。
