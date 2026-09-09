---
type: meta
title: "Wiki Token Discipline"
date: 2026-08-30 16:20
created: 2026-08-30
updated: 2026-09-02
tags:
  - 2026/08/30
  - meta
  - conventions
  - 2026/09/02
status: evergreen
related:
  - "[[conventions]]"
  - "[[index]]"
---

# Wiki Token Discipline

Navigation: [[conventions]] | [[index]]

> [!important] ingest / query / retrieve / lint を実行するエージェントは、`wiki/index.md`・各 `_index.md`・`wiki/log.md`・`wiki/hot.md` の全文を読んではならない。発見はスクリプト、本文は節抽出、カタログ更新は追記コマンドに任せる。

カタログ類は人間と Obsidian 向けの派生物である。エージェントの入力にしてはいけない。

## 禁止する読み出し

次を `Read` してはならない。存在確認・追記・検索もスクリプトか `rg` で行う。

- `wiki/index.md`
- `wiki/log.md`
- `wiki/{sources,entities,concepts}/_index.md`
- `wiki/hot.md` の全文(クイック query だけ、後述の窓を `wiki-catalog.py trim-hot` 済み前提で読んでよい)

例外: カタログ自体を直す保守作業(`wiki-refactor` の索引同期など)で、対象ファイルだけを読むとき。

## 発見: `wiki-resolve.py`

既存ページの有無は索引ではなく名前解決で確認する。

```bash
python3 scripts/wiki-resolve.py "Hellerstein" --type entity --top 8
python3 scripts/wiki-resolve.py "RDMA" --type concept
python3 scripts/wiki-resolve.py "RoCEv2" --type any
```

標準出力は JSON。`candidates[].path` だけを見て、必要なページを `wiki-excerpt.py` で開く。0 件なら新規作成候補とする。aliases とファイル名を優先し、必要なら BM25 を足す。

## 本文: `wiki-excerpt.py`

厚い concept / entity を全文読まない。定義・主題節の outline・受信箱の末尾だけで積み増しできる。

```bash
python3 scripts/wiki-excerpt.py "wiki/concepts/異常検知.md" \
  --sections 定義,子概念,未解決の問い,未編纂の観察 --tail 15 --budget-tokens 1800

# 主題節の地図(## / ### 見出しと、各箇条書きの太字命題行だけ)
python3 scripts/wiki-excerpt.py "wiki/concepts/異常検知.md" --outline
```

- concept の既定節: `定義` / `子概念` / `未解決の問い` / `未編纂の観察`(旧名 `横断的知見` は別名解決され、どちらの名前で指定しても実在する方が返る)
- `未編纂の観察`(旧名含む)と `未解決の問い` は末尾 `--tail` 件だけ
- `--outline` は主題節(固定 6 節以外の `##`)の見出しと、その配下の太字命題行を 1 行ずつ返す。本文は返さない。ingest は追記の前にこれで既存命題を見て、補強・反証する観察なら冒頭に `[節名]` を付ける
- `--budget-tokens`(既定 1800)を超えたら、先に `## 定義` を先頭段落だけにし、なお足りなければ古い箇条書きを落とす。それでも超えた場合は文字数で切り、`excerpt-truncated` 行が付く
- frontmatter は title / type / aliases / address / entity_tier / related 先頭 8 / sources 先頭 8 に圧縮する

ingest の追記先は `## 未編纂の観察`(無ければ旧名 `## 横断的知見`、どちらも無ければ `## 未編纂の観察` を `## 関連` の直前に新設)と `## 未解決の問い` の末尾だけ。主題節と `## 定義` は ingest から書き換えない(conventions §8 更新ルール 2・4)。ページ全体を Edit 用に再読しない。

## カタログ更新: `wiki-catalog.py`

オーケストレータは巨大 Markdown を読まず、追記コマンドだけを呼ぶ。`wiki-catalog.py` は RMW のあいだ `wiki-lock.sh` を自分で取る(競合は同じプリミティブで待ち、取れなければ失敗する。スクリプトが無いときだけ fcntl)。カタログコマンドを `wiki-lock.sh acquire` で包まない。`wiki-lock.sh` は `WIKI_LOCK_VAULT` と `WIKI_VAULT_ROOT` のどちらも見る。

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] ingest-paper | タイトル
- Source: `.raw/papers/<slug>.pdf`
- Summary: [[@YYYY__SOURCE__Title]]
- Pages created: [[A]]
- Pages updated: [[B]]
- Key insight: 一文。
EOF
)"

python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
## YYYY-MM-DD | ingest-paper | タイトル
- Focus: [[@YYYY__SOURCE__Title]]。要点。
- Key insight: 一文。
- New: [[A]]
- Updated: [[B]]
EOF
)"

python3 scripts/wiki-catalog.py prepend-changelog \
  --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest-paper | タイトル\n- 新規 concept: [[A]]\n"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/concepts/_index.md \
  --section "現行コンセプトカタログ" \
  --line "- [[A]]"

python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest-paper | タイトル\n- Source: [[@...]]\n"
```

`prepend-hot` はトークン予算(既定 2000、最大 5 エントリ)で古い窓を落とす。落ちたエントリは `wiki/log.md` に残っている前提であり、`trim-hot` 単体は log へコピーしない。ingest は必ず `prepend-log` してから `prepend-hot` する。

## query の経路

1. **クイック**: trim 済み `wiki/hot.md` だけ。足りなければ「standard で retrieve する」と返す。`index.md` は開かない。
2. **標準 / 深掘り**: `python3 scripts/retrieve.py "<質問>" --top 5` を第一経路にする。ヒットしたページは `wiki-excerpt.py` で読む。concept のハブなら子概念を 1〜2 枚足す。
3. retrieve が exit 10 のときだけ、`wiki-resolve.py` と `rg` でフォールバックする。`index.md` 全文はフォールバックにも使わない。

## ingest の作業集合

1. `wiki-resolve.py` で既存 entity / concept を特定する。
2. 更新するページだけ `wiki-excerpt.py` する。
3. **1 回の取り込みで concept は新規最大 3、更新最大 5。** 溢れた候補は log の `Deferred:` に残し、次の関連ソースで育てる。
4. ハブ concept(おおよそ 50KB 超、または `## 子概念` があるページ)は地図として扱い、具体的な知見は最も近い子へ書く。ハブへは子へのリンクと、子をまたぐ観察だけを足す。
4a. 追記先は `## 未編纂の観察`(旧名 `## 横断的知見`)と `## 未解決の問い` の末尾だけ。主題節は再編纂(`wiki-refactor`)の領分で、ingest は触らない。
5. 書き終わったら `wiki-catalog.py` で log / hot / changelog / catalog line を更新する。
6. 作成・更新したページを `python3 scripts/wiki-retrieve-refresh.py --pages <path> ... --no-llm` に渡し、BM25 を差分更新する。

## entity の stub / full

- `entity_tier: stub` — 初出の共著者・一度きりの言及。frontmatter + 所属/役割 2〜3 行。本文を育てない。
- `entity_tier: full` — 2 ソース目以降、またはハブ(書籍・thesis・繰り返し登場する人/組織/製品)。
- 既存の本文ページは `full` とみなす。`entity_tier` が無いページを遡及改名しない。
- stub の存在確認も `wiki-resolve.py` で行う。本文は excerpt せず frontmatter だけで更新してよい。

## concept の新設閾値

- 単一ソースで閉じる用語は source ページに留める。
- concept を新設するのは、(a) 既存 concept が resolve でヒットしない、かつ (b) 2 ソース以上にまたがるか、今後またがることが明らかなハブ、のときだけ。
- 書籍・thesis で章ごとに用語が出ても、章のたびに新設しない。文書全体で 3 件までを目安にし、残りはハブ entity と章 source に置く。

## 子概念(親子化)

厚い concept は親を地図、子を論題にする。分割の単位は行数ではなく主題の独立性(conventions §7)。

- 親: `## 定義` と `## 子概念` と、子をまたぐ主題節だけを厚くする。
- 子: 独立した手法・層・評価設計などを切り出す。
- 既存の厚い親を今すぐ全部分割しなくてよい。ingest 時は excerpt で親を薄く読み、stats が示すハブは子へ寄せる。
- 機械的な子候補の確認: `python3 scripts/wiki-concept-stats.py --json`。related 先がハブ規模ならピア(子にしない)。相互参照は問わない。

## 再編纂(recompile)の負債

受信箱に観察が溜まったページは、読者が自分で合成し直さないと全体像が得られない。compile 負債の列挙は次で行う。

```bash
python3 scripts/wiki-concept-stats.py --compile-debt            # 受信箱 5 件以上、または主題節なしで 15 件以上
python3 scripts/wiki-concept-stats.py --compile-debt --min-inbox 10
```

出力の各行は `path` / `inbox_bullets`(受信箱の箇条書き数、旧名含む)/ `topic_sections`(固定 6 節以外の `##` の数)/ `legacy_heading`(旧名 `## 横断的知見` が残っているか)を持つ。再編纂は `wiki-refactor` の再編纂様式で 1 ページずつ行い、ingest と混ぜない。

## retrieve の鮮度

`.vault-meta/bm25` が取り込みより古いと、query が外れる。ingest の最後に必ず `wiki-retrieve-refresh.py --pages` を呼ぶ。prefix か BM25 が非 0 なら refresh も非 0。全件作り直しは `wiki-retrieve-refresh.py --all`(wiki 全ページを歩くので、日常の ingest では使わない)。子プロセスは `WIKI_VAULT_ROOT` を見る。
