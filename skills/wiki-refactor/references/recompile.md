# 再編纂(recompile)の手順

`wiki-refactor` skill の従属資料。concept ページ 1 枚の受信箱(`## 未編纂の観察`、旧名 `## 横断的知見`)を主題節の命題へ畳み込み、読者が合成し直さずに現在の理解を得られる参照文書にする。規約の正本は `wiki/meta/conventions.md` §8(主題節の規則 1〜8、更新ルール 4・6)。この文書は手順と検査だけを定める。

subagent に委譲するときは、この文書のパスと対象ページのパスを渡す。subagent はこの文書と「0. 前提」の資料を読んでから着手する。

---

## 0. 前提

読む(順に):

1. `CLAUDE.md`(vault 全体の原則。日本語常体、コードスイッチング回避)
2. `wiki/CLAUDE.md`
3. `wiki/meta/conventions.md` — 特に §5(出典)、§7、§8、§13
4. `wiki/meta/token-discipline.md`
5. `wiki/meta/japanese-style.md`(keep / カタカナ / 漢語の方針)
6. この文書

読まない: `wiki/index.md`、`wiki/hot.md`、`wiki/log.md`、各 `_index.md`。カタログ更新は `scripts/wiki-catalog.py` の追記コマンドだけを使う。

例外: **再編纂は対象 concept ページ 1 枚を全文 `Read` してよい**。受信箱を主題節へ畳むには節全体を見る必要があり、excerpt の末尾 15 件では足りない。全文を読むのは対象ページだけで、姉妹ページは `wiki-excerpt.py --outline` で形だけ見る。

書き込みの前に `bash scripts/wiki-lock.sh acquire <path>`、書き終えたら `release <path>`。

1 回の再編纂 = 1 ページ = 1 コミット。複数ページをまとめない。ingest の途中で行わない。

---

## 1. 現状把握

```bash
python3 scripts/wiki-excerpt.py "wiki/concepts/<page>.md" --outline
python3 scripts/wiki-concept-stats.py --compile-debt --limit 50   # 対象ページの inbox_bullets / topic_sections / legacy_heading
```

そのうえで対象ページを全文読み、次を書き出す(作業メモ。ページには書かない)。

- 受信箱の箇条書きを O1, O2, … と番号付けする。旧名 `## 横断的知見` の配下、`## 未編纂の観察` の配下、および `## 横断的知見（追記）` のような規約外の節の配下をすべて含める。
- 既存の主題節(あれば)とその命題。規約に合わない見出し(「知見」「観察」「考察」「示唆」「まとめ」「その他」「補足」「横断的」を含むもの)は改名または解体の対象。
- `## 定義` が 1 段落か、要約段落を既に持つか。
- `## 未解決の問い` のうち、受信箱の観察で既に答えが出ているもの。

---

## 2. 主題の設計

1. 観察 O1〜On を、**読者が検索語にできる名詞句**で 2〜7 個の主題に束ねる。出発点の雛形は 3 つ(歴史軸: 前史→転回→現在 / 問題構造軸: 問題→解決→限界 / 合意軸: 合意→論争→未解決)だが、雛形より**同じ domain の姉妹 concept の節構成に倣う**ことを優先する。姉妹は frontmatter `related` と `python3 scripts/wiki-resolve.py "<domain 語>" --type concept` で見つけ、1〜2 枚を `--outline` で見る。
2. 主題見出しに次の語を使わない: 知見 / 観察 / 考察 / 示唆 / まとめ / その他 / 補足 / 横断的。「設計上の含意」「実務への示唆」のような過程語も同類として避ける。
3. 1 節 10 命題を超えたら、7 節上限より分割を優先する。7 を超えたら親子化(conventions §13)の候補として報告に書き、それでも節は分ける。34 件を 1 節に押し込んで 7 に収めるのは失敗である。子ページの新設は本作業に含めない。
4. 既存の主題節があるページは、受信箱をその節へ畳むか、10 件超なら既存節を分ける。別の 7 傘に再梱包しない。
5. どの主題にも入らない観察は畳まず、`## 未編纂の観察` に残す(§4)。

---

## 3. 命題の執筆

主題節ごとに、観察を**命題**へ書き換える。

```markdown
## verifier の保証範囲

- **verifier が保証するのは単体安全性であり、共存配備・カーネル版差・プログラム所有メモリの競合は保証範囲外である。**
  - 根拠: [[@2024__SIGCOMM__NetEdit - An Orchestration Platform for eBPF Network Functions at Scale]] — 百万台規模の共存にはライフサイクル管理層が別途要る
  - 根拠: [[@2026__KubeCon Japan Community Day__XDPerf - A High-Performance Traffic Generator Built with WASM and eBPF]] — 6.1〜7.2 の multi-kernel CI が必要だった
  - 根拠: [[@2025__PhD__Scaling Telemetry Workloads in Cloud Applications - Chapter 3 Efficient TCP-UDP Socket-based Instrumentation in Kernel for Continuous Construction of Network Call Graphs]] §3.3.2 — マップ値の競合は verifier 対象外、3 層の同期で埋めた
```

規則:

- 命題行は太字の一文で「何が真か」を述べる。用語ラベル(「成功率」「SLE」)や見出しの再掲を命題にしない。対比(A と B を並べると X が見える)は根拠に降格させ、命題には書かない。命題の主語は対象世界の事物にする。「本 wiki の X 層が観測した…」「本 vault では…」のように wiki 自身を主語にしない(それは自己言及の変種)。
- 既存節に残っていた散文・番号付きリスト・用語解説は、命題形式に直すか根拠に降格する。主題節に再編纂前の地の文を残さない。
- 畳み込みは圧縮が原則。M(命題) が N(観察) 以上なら、複数観察を 1 命題の複数根拠へ寄せ直す。観察をほぼ 1:1 で命題化しただけなら未完了である。
- 根拠は `- 根拠: [[@source]] — 一行` の入れ子。反証・留保は同じ入れ子に `- 反証:` / `- 留保:`。姉妹 concept への横参照は `- 関連: [[concept]] — 一行` にし、`留保:` を横参照の置き場にしない。
- 根拠の説明は 1〜2 文に収める。数値・条件・図表番号は残し、背景説明や言い換えは削る。長くなるなら命題を分ける。
- 1 主題節の命題は 3〜8 件を目安にする。10 件を超える節は主題が 2 つ同居している(例: 「方針制御への拡張」と「verifier の保証範囲」)ので分ける。逆に 1〜2 件しかない節は隣の節へ寄せる。
- 見出しの和文と英字・数字の間には半角空白を入れる(「eBPF による」「成熟モデルの 2 軸」)。本文の表記と揃える。
- **畳んだ観察が引いていた出典は、命題の根拠にすべて引き継ぐ。** 1 つも落とさない(§5 で機械的に検査する)。
- 観察 1 件が命題 1 件になるとは限らない。3 件の観察が 1 命題の 3 根拠になることも、1 件の観察が 2 命題に分かれることもある。
- 矛盾は `> [!contradiction]` callout のまま残す。散文に溶かして消さない。
- ページの自己言及(「既出」「本ページが集約してきた」「本頁に初めて追加する」「本ページ既出の…」)を書かない。比較の履歴は log が持つ。
- 日本語常体。英単語を和文に素のまま混ぜない(japanese-style.md)。略語・固有名詞・数式・コード・wikilink は原語のまま。
- 主題節の中で `###` を使ってよいが、`###` の配下にも同じ命題形式を使う。

`## 未解決の問い` は、受信箱の観察で答えが出た問いを落とし、再編纂で新たに見えた問いを足す。答えの出た問いの内容は該当する命題の根拠に移す。問い節に、すでに出典付きで答えが書いてある項目(命題形式)があれば主題節へ移し、問いリストから外す。既存の問い節に「本ページが蓄積する」「本ページが扱う」のような自己言及が残っていれば、畳み込みと同時に書き換える。検査 2 の対象は主題節だけでなく問い節も含む。

---

## 4. 受信箱と退避

1. 畳めなかった観察(単一ソースで裏取りが要る、主題が立たない)だけを `## 未編纂の観察` に残す。残す理由を各項目の末尾に `— 保留理由:` で一言書く。残す観察が 0 件なら、見出しの直後に折り畳み callout だけを置く。「保留した観察: 0件」のような状況報告の箇条書きは置かない(受信箱件数に数えられる)。
2. 畳んだ元の箇条書きは**原文のまま**、`## 未編纂の観察` の末尾に折り畳み callout として退避する。

```markdown
> [!note]- 編纂前の観察 2026-09
> - (O1 の原文)
> - (O2 の原文)
> …
```

`> [!note]-` の `-` が折り畳みの印。callout 内の行頭は `> ` で始めるので、受信箱の件数(`^- `)には数えられない。この callout は次回の再編纂で削除してよい。

3. 旧名 `## 横断的知見` の見出しと、規約外の節見出し(`## 横断的知見（追記）` 等)を除去する。節の並びを次に揃える: `定義` → `子概念`(あれば) → 主題節 → `未解決の問い` → `未編纂の観察` → `関連` → `出典`。

---

## 5. 定義の要約段落と frontmatter

- 主題節が 3 つ以上あるページは、`## 定義` の 2 段落目に現在の理解の要約(合意していること・争点・分野の移動)を 3〜6 文で書く。主題節の命題を読めば復元できる内容に留め、新しい主張を持ち込まない。主題節が 2 つ以下なら書かない。
- frontmatter: `updated: YYYY-MM-DD` を更新し、`tags` に日付タグ `YYYY/MM/DD` を追加し、`recompiled: YYYY-MM-DD` を置く(再編纂だけが更新する項目)。`sources:` を本文の `[[@...]]` と同期する。`address` は変えない。

---

## 6. 検査(書き終えてから、主張の前に必ず実行する)

```bash
P="wiki/concepts/<page>.md"

# 1. 出典を 1 つも落としていないか(再編纂前の wikilink 集合 ⊆ 再編纂後)
python3 - "$P" <<'EOF'
import re, subprocess, sys
p = sys.argv[1]
before = subprocess.run(["git", "show", f"HEAD:{p}"], capture_output=True, text=True).stdout
after = open(p, encoding="utf-8").read()
link = re.compile(r"\[\[([^\]|#]+)")
lost = sorted(set(link.findall(before)) - set(link.findall(after)))
print("lost wikilinks:", len(lost))
for name in lost:
    print("  -", name)
sys.exit(1 if lost else 0)
EOF

# 2. 自己言及・汎用見出しが残っていないか。
#    退避 callout(行頭 `>`)の中は原文保存なのでヒットしてよい。`rg -v '^[0-9]+:>'` で除外した残りが 0 であること。
#    「本ページが集約」だけでなく「本ページが蓄積/記録/扱う」「本 wiki」も対象。問い節も含む。
#    見出しは 未編纂の観察 のヒットだけなら可。旧名 横断的知見 は 0。
rg -n '既出|本ページ|本頁|本 wiki' "$P" | rg -v '^[0-9]+:>'
rg -n '^## .*(知見|観察|考察|示唆|まとめ|その他|補足|横断的)' "$P" | rg -v '未編纂の観察'

# 3. 形が規約どおりか
python3 scripts/wiki-excerpt.py "$P" --outline
python3 scripts/wiki-concept-stats.py --compile-debt --limit 200 | rg -n "<page>"   # 出なければ負債解消

# 3b. 1 節 10 命題超が残っていないか。出たら節を分ける(7 節上限より優先)。
python3 scripts/wiki-excerpt.py "$P" --outline | python3 -c "
import sys, re
text = sys.stdin.read()
bad = []
for line in text.splitlines():
    m = re.search(r'^## (.+?)  \((\d+) 命題\)', line)
    if m and int(m.group(2)) > 10:
        bad.append(f"{m.group(1)}: {m.group(2)}")
print('oversized sections:', len(bad))
for item in bad:
    print('  -', item)
sys.exit(1 if bad else 0)
"
```

検査 1 が失敗したら、落ちた出典を該当命題の根拠へ戻す。検査 2 で自己言及が出たら書き直す。

---

## 7. 記録とコミット

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] recompile | <page>
- Page: [[<page>]]
- Folded: N 観察 → M 命題 / K 主題節(<節名 1>, <節名 2>, …)
- Kept in inbox: J(保留理由の要約)
- Parked: `> [!note]- 編纂前の観察 YYYY-MM`(N 件)
- Summary paragraph: rewritten | not needed(主題節 2 つ以下)
- Dropped questions: 未解決の問いから落とした件数と行き先
EOF
)"

python3 scripts/wiki-retrieve-refresh.py --pages "wiki/concepts/<page>.md" --no-llm

git add "wiki/concepts/<page>.md" wiki/log.md
git commit -m "wiki: recompile <page>"
```

対象ページと `wiki/log.md` 以外を stage しない。vault には無関係な未コミット変更が常にあるので `git add -A` を使わない。

記録の数え方: 「N 観察」は受信箱(旧名・規約外の節を含む)から畳んだ箇条書きの総数、「M 命題」は再編纂後の主題節にある太字命題の総数(既存節に足した分も含む)。観察と命題は 1 対 1 ではない(複数観察が 1 命題の複数根拠になる、1 観察が 2 命題に分かれる)。M は暗算しない。log を書く直前に次を実行し、出力の整数をそのまま貼る。log は追記専用なので、数え違いを後から直せない。

```bash
python3 scripts/wiki-excerpt.py "$P" --outline | python3 -c "
import sys, re
print(sum(int(n) for n in re.findall(r'\((\d+) 命題\)', sys.stdin.read())))
"
```

---

## 8. 報告

呼び出し元へ返すもの(ファイルには書かない):

- 畳み込み比(N 観察 → M 命題 / K 主題節)と主題節名
- 受信箱に残した件数と理由
- 検査 1〜3 の結果(落ちた出典 0、自己言及 0、汎用見出し 0、compile 負債から消えたか)
- 親子化候補として報告すべき事項(主題が 7 を超えそう、子ページが要りそう)
- 規約側に見つけた不備(手順どおりに進まなかった箇所)

---

## 9. やらないこと

- 他のページ(姉妹 concept、source、entity)を編集しない。子ページの新設もしない。
- `wiki/index.md`・`hot.md`・`log.md`・`_index.md` を `Read`/`Edit` しない。
- 出典のない主張を新たに生まない。「一般に知られている」で埋めない。
- `> [!contradiction]` を消さない、言い換えで矛盾を無くさない。
- 受信箱に残した観察を勝手に削除しない。退避 callout の原文を書き換えない。
- 複数ページを 1 コミットにまとめない。
