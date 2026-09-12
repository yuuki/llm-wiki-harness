# 論文担当 subagent の手順書

`wiki-ingest-paper` の従属資料。バッチ並行の 1 論文担当 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL を渡さない。索引・commit・バッチ全体の catalog 更新は書かない。

あなたは Obsidian vault（作業ディレクトリ = vault ルート）で、論文 1 本の wiki 取り込みを担当する。

## 必読

- `wiki/meta/conventions.md`
- `wiki/meta/japanese-style.md`
- ベクター図のクロップは [`figures.md`](figures.md)

本文は日本語・常体。

## Bash

毎回 vault ルートから実行する。`cd` だけの呼び出しは禁止。必ず実処理を同じ呼び出しに置く。

```bash
cd "$VAULT_ROOT" && ...
```

## 原本取得

```bash
bash scripts/fetch-paper-pdf.sh "<arxiv-url|id|pdf-url|local-pdf>"
```

fetch が `page-*.png` の掃除まで行う。手で `find` / `python -c` しない。保持されるのは `image-*.png` と `images.json` だけ。`scripts/allocate-address.sh` は直接実行しない(採番は `wiki-page-write.py` の内側)。

`pages=` が 30 以上かつ章相当の節構造、または博士/修士論文なら、このセッションでは続けない。`fetch-book.sh` を呼ばない。章ページを書かない。報告は `Deferred: wiki-ingest-thesis を新セッションで`。

## 書き込み

- 新規: `python3 scripts/wiki-page-write.py --batch spec.json`
- 既存追記: `python3 scripts/wiki-append.py --batch spec.json`

**ヘルパーを lock で包まない。** **新規を `Write` で作らない**(page-write だけ)。**1 ページに複数回 Edit しない**。resolve は `--compact` で一括。excerpt は複数ページ 1 回。カタログ全文と `.vault-meta/clusters.json` は Read しない。`allocate-address.sh` の採番はヘルパー外では失敗する。テーマ塊は下の節に従え。

## 画像

```bash
python3 scripts/wiki-verify-ingest.py --list-figure-ids ".raw/papers/<slug>.txt"
python3 scripts/contact-sheet.py --manifest ".raw/papers/<slug>/images/images.json" --out "$TMPDIR/cs-<slug>" --index-txt
```

**個別 Read の前に**シートを Read する。`image-*.png` / attachment をシート無しで Read してはならない。シートと `index.txt` で番号が付いたファイルは Read しない。クロップ結果も位置が疑わしいときだけ。`individual-reads` は `sheet-*.png` を除く画像 Read の実数。シート 0 枚なら個別 Read も 0。回数 1 はキャプションのみ候補として目視する。ベクターは [`figures.md`](figures.md) のクロップ。「## 図表」セクションは作らない。埋め込みは該当記述の直後。

除外(これ以外は本文参照分をすべて取り込む):

- 本文参照なし(装飾・著者近影・ロゴ・体裁上の飾り)
- 凡例のみ、ページ全体スクショ
- コードブロックで代替できるコード画像
- 付録のみ参照(本文主張に直結するなら含めてよい)
- ほぼ同一構図の繰り返し(代表 1〜2 点。省略数は報告に書く)

attachment は `wiki/sources/_attachments/<slug>/figNN-....png`。ファイル名に `token`・`secret`・`password`・`credential`・`key` を含めない。

## source ページ

ファイル名は `wiki/sources/@YYYY__SOURCE__Title.md`。wiki 内リンクはすべて `@` 付き。

frontmatter: `address:`(ヘルパー採番)、日付タグ先頭、`type: source`、`source_type: paper`、`sources` に PDF / txt / `images.json`。

**abstract は全文和訳**(一文ずつ忠実。原文を要約・補完しない)。

```markdown
> [!abstract] 概要(abstract の日本語訳)

## 論文情報
## 概要
## 問題設定
## 提案手法
## 新規性
## 実験設定
## 実験結果
## 考察
## 強み / 弱点・課題
```

出典に無いことは書かない。

## entity

`entity_tier`: 初出の共著者・一度きりの言及は `stub`(frontmatter + 所属/役割 2〜3 行)。繰り返し登場する組織/製品/ハブは `full`。

書き込み直前に resolve し直す。HIT / page-write が既存拒否 → append だけ。NONE → page-write だけ。`Write` で新規を作らない。他セッションに上書きされたらマージ復旧せず止める。

## concept

追記は `wiki-append.py` だけ。対象は `## 未編纂の観察` と `## 未解決の問い`。**主題節を書き換えない。** **単一ソース事実は受信箱に書かない。** 単一論文で閉じる用語は source に留める。**新規最大 3 / 更新最大 5。** 溢れたら log 用に `Deferred:` を報告する。**ハブ concept は子へ書く**(ハブへは子へのリンクと子をまたぐ観察だけ)。矛盾は `> [!contradiction]`。`- 概念:` は下のテーマ塊手順に従う。

## テーマ塊

`.vault-meta/clusters.json` を Read するな。見るなら CLI。失敗したら resolve / retrieve だけに落ちる。wiki-survey の精読クラスタと混同するな。Gap Finder の `#id` と突合するな。`wiki-clusters.py assign` は呼ぶな。

対象は今回新規作成または更新する concept だけである。entity / source は `lookup` しない。frontmatter `related:` と `community:` は書かない。ハブ側へ相互 related は足さない。

1. 今回の `wiki-resolve.py --compact` が当てた**既存 concept** を score 降順で最大 5 件 `lookup` する
2. 直前の refresh 済みキャッシュを信じる。`lookup` が未構築で終了 3 のときだけ `python3 scripts/wiki-clusters.py build` を 1 回試す。ページ欠落の終了 3 では建て直さない。stale では建て直さない。refresh の `clusters_ok` が false ならテーマ塊を使わない
3. `on_backbone: true` の id について `python3 scripts/wiki-clusters.py members <id> --type concept --top 20`。自分・今回新規名・既存の `- 概念:` を除く
4. resolve 集合に無いハブを `members` 順で最大 3 件取る。4 件目以降は開かない。すでに excerpt 済みのハブは追加読みしない。更新対象と同じ `wiki-excerpt.py` に載せる。`--budget-tokens 1800`。`--total-budget` は `(更新する concept 数 + 追加ハブ数) * 1800` 以上
5. excerpt が今回 source の主題に接するものだけを `- 概念:` に足す。切れたページは読んだことにせず related に足さない
6. 1 ページの `- 概念:` は既存込み最大 5、うち塊由来は最大 3。この 3 は concept 新規 3 / 更新 5 の枠を消費しない
7. 新規は `wiki-page-write.py` 初回本文の `## 関連` 行 `- 概念: [[...]] / [[...]]`。更新は同じ append batch の `related.概念`。別 Edit を増やさない

未構築のまま / 既存 concept 0 / 全て `on_backbone: false` / 接するハブ 0 なら、今どおり resolve 結果だけを related にする。JSON を開けて補完してはならない。

## 触ってはいけないファイル

`wiki/index.md`、各 `_index.md`、`hot.md`、`log.md`、`overview.md`、`.raw/.manifest.json`。**catalog を subagent が書かない。** **`papers/` `research/` `structures/` `notes/` を書き換えない。**

## verify

```bash
python3 scripts/wiki-verify-ingest.py --pages "wiki/sources/@YYYY__SOURCE__Title.md" \
  --attachments-slug <slug> --figure-ids-from ".raw/papers/<slug>.txt"
```

## git

`git add` / `git commit` / `git stash` / `git checkout` 禁止。確認の `git status` / `git diff` のみ。

## 完了報告

作成・更新したページ(すべて) / 一行の key insight / 気づいた矛盾 / 図表の総数と除外理由 / Deferred / `contact-sheet: N sheets / individual-reads: K`(K はシートを除く画像 Read の実数)。「これから〜する」で終えない。
