# 命題の再検証(claim audit)の手順

`wiki-refactor` skill の従属資料。concept ページの主題節にある太字命題を標本抽出し、命題が引く出典に**本当にその記述があるか**を照合する。再編纂は観察を命題へ畳む工程で、一般化・数値のずれ・wiki 側の解釈の混入を起こしうる。本手順はそれを検査する。

判定は「命題が真か」ではなく「引かれた出典がその命題を支持するか」である。命題が正しくても出典が違えば `not_in_source` になる。

---

## 0. 前提

読む: `CLAUDE.md`、`wiki/CLAUDE.md`、`wiki/meta/conventions.md` §5・§8、`wiki/meta/token-discipline.md`、この文書。

読まない: `wiki/index.md`、`wiki/hot.md`、`wiki/log.md`、各 `_index.md`。対象 concept ページも全文 Read しない。パケットの抜粋で足りないときだけ source ページを `wiki-excerpt.py` で見て、それでも足りなければ `.raw/` の抽出テキストを `rg` で引く(§2 の条件と手順)。`.raw/` の PDF と画像は開かない。

スクリプトは wiki の本文ページを書かない。書くのは台帳 `.vault-meta/claim-audit.json`(git 追跡、`-f` で add)と報告書 `wiki/meta/claim-audit-YYYY-MM-DD.md` だけである。命題への手当て(§4)は本手順の外で、wiki-refactor の通常規則(lock、1 ページ = 1 コミット)に従って行う。

---

## 1. 標本抽出

```bash
# 出力先は作業者ごとの一時ディレクトリにする。/tmp の固定名は並列の作業どうしで上書きし合う。
W="$(mktemp -d "${TMPDIR:-/tmp}/claim-audit.XXXXXX")"

# 再編纂直後の自己検査(対象ページの命題を全部)。--per-page はページの命題数以上にする。
# 足りないと上限を超えた命題が黙って漏れる(既定は 3)。
python3 scripts/claim-audit.py sample --pages "wiki/concepts/<page>.md" --per-page 500 > "$W/claim-sample.json"

# 定期検査(lint から。直近に再編纂されたページを新しい順に 5 頁 × 2 命題)
python3 scripts/claim-audit.py sample --recompiled --limit 5 --per-page 2 --seed "$(date +%Y%m%d)" > "$W/claim-sample.json"

# 抜き打ち(concept をランダムに 10 頁 × 1 命題)
python3 scripts/claim-audit.py sample --random 10 --per-page 1 --seed "$(date +%Y%m%d)" > "$W/claim-sample.json"
```

出力の `claims[]` は 1 件が「命題 + 根拠行 + 出典ごとの根拠候補」である。

- `claim`: 太字命題の本文。`section`・`line` は所在。
- `sources`: 命題行と `- 根拠:` / `- 反証:` 行が引く source ページ名。`- 関連:` は含めない。パス付きの形(`[[wiki/sources/@X|表示]]`)もファイル名だけの `[[@X]]` と同じ出典として数える。
- `evidence[]`: 出典ごとに `status`(ok / stub / source_missing / unreadable)、`page_excerpts`(source ページで命題と語彙が重なる段落・箇条書き上位 3 件)、`raw_text`(抽出テキストのパス。無ければ null)、`raw_excerpts`(抽出テキストで同様に上位 3 件)。
- `numbers`: 命題行と `- 根拠:` / `- 反証:` 行に出てくる数値の機械照合。根拠行の数値はその行が引く出典に、命題行の数値は命題の出典全体に照らす(`- 留保:` と `- 関連:` は照らさない)。
  - `missing`: source ページにも抽出テキストにも無い数値。`where` は `命題` か `根拠[i]`(`evidence_lines` の添字)。`raw_available: false` は抽出テキストが無く、ページだけで照らした印。
  - `raw_only`: source ページには無く、抽出テキストにだけある数値。source ページが原文の一部しか要約していない印であり、ページに無いことを根拠に `not_in_source` としてはならない。
  - 年の範囲で後ろの年を省略した形(`1984〜86 年`、`2019-21`)は展開してから照らす(`86` は `1986`)。展開して前の年より前になる形(`2024-05` の年月)は展開しない。
  - 照合は字面ではなく値で行う。`1.50` と `1.5`、`10K` と `10,000` と `10 thousand`、`1,000 万` と `10 million`、`35%` と `0.35`、`two` と `2` は同じ値とみなす。出典側の小数は丸めを許す(`0.4837` は `0.48` に当たる)。`3.3.2` のような節番号は字面で照らす。wikilink の中の年、ISO 日付、`p.12` の頁番号、DOI・arXiv ID、`H100` のような英字直後の数字は照らさない。
  - 単位換算(`200µs` と `0.2 ms`)、和や比として導いた値(`34.72% + 24.72% = 59.44%`)、漢数字は拾えない。`missing` は判定ではなく「§2 で確かめる数値」の一覧である。

台帳に判定済みの命題は既定で除かれる。同じ命題を再判定するときだけ `--reaudit`。

数値の照合だけを先に通したいとき(再編纂の直後、判定の前)は、パケットを作らずに `numbers` を使う。終了 1 は `missing` がある印。

```bash
python3 scripts/claim-audit.py numbers --pages "wiki/concepts/<page>.md"
```

---

## 2. 判定

命題 1 件ごとに、`evidence` を読んで次のいずれかを付ける。

| 判定 | 意味 | 典型 |
|---|---|---|
| `supported` | 出典の記述が命題を支持する | 数値・条件・向きが一致 |
| `unsupported` | 出典の記述が命題と食い違う | 数値が違う、条件が落ちて一般化されている、因果の向きが逆 |
| `not_in_source` | 出典にその内容が無い | 2 出典の各半分は書いてあるが「A は B を圧縮したものだ」という対応づけ自体はどちらにも無い(wiki 側の解釈を事実の形で書いている) |
| `source_missing` | source ページが存在しない | 改名漏れ、未 ingest |
| `unclear` | 抜粋では決められない | source ページと抽出テキストを下の順に見て再判定し、それでも決まらなければ `unclear` のまま残す |

確かめる順序:

1. パケットの `page_excerpts` と `raw_excerpts`。
2. 決まらなければ `python3 scripts/wiki-excerpt.py "<source page>" --budget-tokens 1500` で source ページを見る。
3. それでも決まらないとき、または次のいずれかに当たるときは、抽出テキストを `rg` で引く。
   - `numbers.missing` か `numbers.raw_only` に数値が出ている(丸め・換算・導出値かどうか、どの節の値かを確かめる)。
   - `not_in_source` か `unsupported` を付けようとしている。source ページは原文の一部しか要約していないことがあるので、ページに無いだけでは決めない。抽出テキストに無いことを確かめてから付ける。
   - 命題か根拠行が節番号・章番号・帰属(誰が・どの手法が・どの規模で)を言い、抜粋にそれが見えない。
4. 抽出テキストが無い(`raw_text` が null)か、`rg` でも決まらなければ `unclear`。PDF と画像は開かない。

抽出テキストの引き方:

```bash
# パスはパケットの evidence[].raw_text を写す。章別テキスト(chapters/ch-NN.txt)で見つからなければ同じ書籍の toc.txt も引く。
rg -n -C2 -i '64 GPUs?|heterogeneous cluster' "<raw_text>" | head -40
```

- 語は原文の言語で引く(英語論文なら英語)。数値は表記揺れを正規表現で吸収する(`200 ?(µs|us)`、`10,?000`)。
- 1 命題あたり `rg` は 3 回までを目安にし、出力は `head` で切る。抽出テキスト全体を Read しない。
- 抽出テキストでだけ確かめられたときは、判定は抽出テキストに従い、`note` に「raw §N で確認(source ページに無い)」と書く。source ページの薄さは §5 の報告に出典側の問題として挙げる。
- 抽出テキストは PDF の機械抽出なので、表の列がずれて数値の対応が崩れていることがある。表の値で決めるときは行見出しと列見出しの両方が同じ行に読めることを確かめ、読めなければ `unclear`。

規則:

- `evidence[].status` が `stub` の出典は wiki からは検証できない。その出典だけが根拠なら `unclear`、他の出典で支持されるなら `supported` とし、note に「stub 未検証」と書く。
- 命題が複数出典を束ねる場合、**全部の出典で各部分を確認**してから `supported` にする。半分しか見ていないなら `unclear`。
- `note` は一文。判定の根拠(どの抜粋のどの記述)か、`not_in_source` なら何が出典に無いのかを書く。次の担当者が同じ判断に到達できる粒度にする。
- 命題そのものの真偽で判定しない。世間で正しいと知っていても、出典に無ければ `not_in_source`。
- **wiki 側の解釈を `- 留保:` や `- 関連:` へ降格した命題**は、降格前の主張(観察が事実の形で書いていた対応づけ・評価語)に対して `not_in_source` を付ける。降格後の命題に対して `supported` を付けない(recompile.md §3・§6 と同じ規則)。`note` は「〜の対応づけは出典に無い → 手当て: 留保へ降格」の形にし、finalize で resolve する。
- **raw が原典でない出典**(raw が二次資料の要約・複数事例の寄せ集め・要約スタブで、原文の抽出テキストではないもの)は、`raw_text` があっても原文照合ができない。raw でだけ確かめられた主張は `supported` にせず `unclear` とし、`note` に「raw が原典でない(要約のみ)」と書く。source ページと raw が一致していても同じ扱いにする。§5 の報告に出典側の問題として挙げる。
- 判定するのは命題である。根拠行の数値・節番号・帰属が出典と食い違っても、命題が出典で支持されるなら `supported` にし、`note` を「根拠行: 32 GPU は出典では 64 GPU」のように `根拠行:` で始める。根拠行の直しは §4 で行う。命題がその誤った値に依っているなら(命題にも同じ値がある、値を外すと命題が成り立たない)、命題を `unsupported` にする。

判定は JSON に書く。`id`・`page`・`line`・`sources` はパケットからそのまま写す。

- `verdict` は sample を取った時点の命題に対する判定を書く。§4 の手当てで命題を直しても、`supported` に書き換えない。台帳の問題率は「命題がどれだけ出典から外れていたか」を測る数字なので、手当て後の判定で上書きすると消える。手当て済みかどうかは `resolve` が持つ(§4)。
- `claim` は、判定と同じ作業の中で命題を直したなら直した後の文、直していないならパケットの文にする。
- 再編纂で観察から命題を新しく起こした場合は、直す前の命題が無い。元になった観察の主張に対して判定し、観察の誤りを正した点を `note` に書く(recompile.md §6)。
- 複数の観察を 1 命題に束ねた場合は、命題に持ち込んだ部分(主語・数値・条件)ごとに出所の観察の主張を照らし、束ねた観察のうち最も悪い判定を付ける(`unsupported` → `not_in_source` → `source_missing` → `unclear` → `supported` の順)。主な出所の観察だけで判定しない。`note` に束ねた観察の番号と、食い違った観察とその点を書く(recompile.md §6)。
- 再編纂で旧主題節の出典の無い散文・表から命題を起こした場合は、旧節に書かれていた主張に対して、根拠に付けた source が支持するかで判定する(recompile.md §3)。
- 命題文を直すと次回の `sample` では別の id になる(id は命題文から作られる)。直した命題は次回、未判定の新しい命題として出てくる。これは想定どおりで、手当て後の文を改めて検査する機会になる。

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

`record` は台帳に追記し(同じ id の再判定は `history` に前回を残す)、`wiki/meta/claim-audit-YYYY-MM-DD.md` を当日分から再描画する。報告書は手で編集しない。どこで `record` を打つかは、検査の種類で分かれる。

**再編纂直後の自己検査**では、ここで `record` を打たない。判定 JSON を `recompile.md` §7 の `recompile-finalize.sh --verdicts` に渡す。スクリプトが、再編纂したページ・log・キューと同じコミットに台帳を入れる。手で `record` を打ってから確定すると、同じ判定が二重に記録される。

**定期検査・抜き打ち**(再編纂を伴わない)では、`record` を直接打ち、台帳と報告書をパス限定でコミットする。

```bash
python3 scripts/claim-audit.py record --verdicts "$W/claim-verdicts.json" --by agent   # 人間が判定したら --by human
python3 scripts/claim-audit.py report                                                  # 累計と問題率
R="wiki/meta/claim-audit-$(date +%F).md"
git add -f .vault-meta/claim-audit.json "$R"
git commit -m "wiki: record claim audit $(date +%F)" -- .vault-meta/claim-audit.json "$R"
```

`git add -A` は使わない。index.lock の競合で commit が失敗したら、同じコマンドを再試行する。台帳が未コミットのまま次の作業に移らない。

---

## 4. 手当て(supported 以外)

命題を黙って直さない。conventions §8 更新ルール 4 のとおり、痕跡を残して直す。

| 判定 | 手当て |
|---|---|
| `not_in_source` / `unsupported` | 命題の根拠入れ子に `- 留保: 再検証 YYYY-MM-DD — <出典に無い/食い違う点を一文>` を足す。命題文を弱める(「X は Y を圧縮したものである」→「X と Y は同じ順序の逆転を述べる」)のは再編纂の一部として行い、元の文は退避 callout に残す。出典の記述と**正面から食い違う**なら `> [!contradiction]` callout を命題の直下と source ページの両方に置く(§5)。 |
| `source_missing` | `python3 scripts/wiki-resolve.py "<name>" --type source` で改名先を探し、wikilink を直す。無ければ lint の dead link として扱う。 |
| `unclear` | 何もしない。次回の `--reaudit` で再判定する。 |
| `note` が `根拠行:` で始まる(判定は問わない) | 根拠行の数値・節番号・帰属を出典の記述どおりに直す。根拠行は命題ではないので退避 callout は要らないが、コミットメッセージに直した値を書く(`wiki: fix evidence numbers in <page> after claim audit`)。判定が `supported` なら `resolve` は打たない。 |

手当ての書き込みは `bash scripts/wiki-lock.sh acquire <path>` / `release <path>` で囲む。手当てを終えたら、コミットの前に台帳に記し、ページと台帳を同じコミットに入れる(1 ページ = 1 コミット)。`resolve` をコミットの後に打つと、台帳の変更がどのコミットにも入らずに残る。

**再編纂直後の自己検査**では、`resolve` を打たない。`recompile-finalize.sh` に `--resolve <id> "<手当て>"` を付ける(`recompile.md` §7)。

**定期検査・抜き打ち**では、次のようにする。

```bash
P="wiki/concepts/<page>.md"
python3 scripts/claim-audit.py resolve <id> --note "留保を付けた"   # 手当てした命題ごと
git add -f "$P" .vault-meta/claim-audit.json
git commit -m "wiki: add 留保 to <page> after claim audit" -- "$P" .vault-meta/claim-audit.json
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
- 判定のために `.raw/` の PDF と画像を開かない。抽出テキストは §2 の条件で `rg` で引くだけにし、全体を Read しない。`rg` でも決まらなければ `unclear`。
- source ページに無いことだけを根拠に `not_in_source` にしない(抽出テキストがあるなら `rg` で確かめる)。
- 台帳と報告書を手で編集しない。
- 手当てを本手順の中で複数ページまとめてコミットしない。
- source ページを書き換えない(contradiction callout を置く場合を除く)。
