# 論文担当 subagent の手順書

`wiki-ingest-paper` の従属資料。バッチ並行の 1 論文担当 subagent が読む**唯一の手順書**である。オーケストレータはフル SKILL を渡さない。索引・commit・バッチ全体の catalog 更新は書かない。

あなたは Obsidian vault(オーケストレータが個別プロンプトで渡す vault ルートの絶対パス)で、論文 1 本の wiki 取り込みを担当する。

## 必読

- `wiki/meta/conventions.md`
- `wiki/meta/japanese-style.md`
- ベクター図のクロップは [`figures.md`](figures.md)。`scripts/crop-paper-figures.py` を使い、ページ全幅の手クリップはしない

本文は日本語・常体。

## Bash

毎回 vault ルートから実行する。`cd` だけの呼び出しは禁止。必ず実処理を同じ呼び出しに置く。

```bash
cd <vault ルートの絶対パス> && ...
```

## 原本取得

```bash
bash scripts/fetch-paper-pdf.sh "<arxiv-url|id|pdf-url|local-pdf>"
```

ACM / IEEE の PDF をブラウザで落とした論文は、オーケストレータがローカルパスと出自を渡す。そのときは `FETCH_ORIGIN_REF="<渡された origin_ref>" bash scripts/fetch-paper-pdf.sh "<ローカル PDF>" "<slug>"` と呼ぶ。fetch が終了コード 5(`browser_fetch=` を出力)を返したら、ブラウザを操作せずに止まり、`Deferred: browser fetch (<browser_fetch の値>)` と報告する。

fetch が `page-*.png` の掃除まで行う。手で `find` / `python -c` しない。保持されるのは `image-*.png` と `images.json` だけ。`scripts/allocate-address.sh` は直接実行しない(採番は `wiki-page-write.py` の内側)。

fetch の出力の `text_quality=` が `ok` 以外なら、本文は `.txt` ではなく `ocr_txt=`(`<slug>.ocr.txt`)を読み、`sources:` に足す。`ocr_status=failed` ならサンドボックス無効化で `bash scripts/pdf-ocr.sh .raw/papers/<slug>.pdf .raw/papers/<slug>.ocr.txt` を再実行し、それでも無理なら PDF をページ指定で `Read` して目視で読む。どれもできなければ止まり、`Deferred: text layer unusable` と報告する。**訓練知識や記憶から本文を書かない。** OCR 論文では要の数値(abstract・主結果)を PDF のそのページの目視で確かめる。以下の `<slug>.txt` は OCR 論文では `<slug>.ocr.txt` と読み替える。

fetch の出力の `existing_sources=` が空でなければ、その論文は既に wiki にある。新しい source ページを作らず、挙がった既存ページを更新する(本文の追記、図表の補完、`updated:` の更新。address は再採番しない)。完了報告に `Existing: <既存ページ>` を書く。

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

**個別 Read の前に**シートを Read する。`image-*.png` / attachment をシート無しで Read してはならない。シートと `index.txt` で番号が付いたファイルは Read しない。クロップ結果も位置が疑わしいときだけ。`individual-reads` は `sheet-*.png` を除く画像 Read の実数。シート 0 枚なら個別 Read も 0。回数 1 はキャプションのみ候補として目視する。ベクターは [`figures.md`](figures.md) のヘルパーでクロップする。`review: true` または位置が疑わしい結果だけ Read する。「## 図表」セクションは作らない。埋め込みは該当記述の直後。

除外(これ以外は本文参照分をすべて取り込む):

- 本文参照なし(装飾・著者近影・ロゴ・体裁上の飾り)
- 凡例のみ、ページ全体スクショ
- コードブロックで代替できるコード画像
- 付録のみ参照(本文主張に直結するなら含めてよい)
- ほぼ同一構図の繰り返し(代表 1〜2 点。省略数は報告に書く)

attachment は `wiki/sources/_attachments/<slug>/figNN-....png`。

## source ページ

ファイル名は `wiki/sources/@YYYY__SOURCE__Title.md`。wiki 内リンクはすべて `@` 付き。

frontmatter: `address:`(ヘルパー採番)、日付タグ先頭、`type: source`、`source_type: paper`、`sources` に PDF / txt / `images.json`。

**raw の Abstract は本文先頭を `> [!abstract] 概要` callout にして全文和訳する**(一文ずつ忠実。原文を要約・補完しない)。タイトルは `概要` のみ。括弧書きの補足は付けない。`## Abstract` は作らない。`## 概要` は wiki 側の 2-3 文要約であり、論文 Abstract の置き場ではない。

本文の先頭行に H1 を 1 つだけ置く(frontmatter の `title` と同じ文字列)。H1 が無いと `wiki-page-write.py` が拒否する。

```markdown
# {frontmatter の title と同じ文字列}

> [!abstract] 概要
> {raw の Abstract を一文ずつ忠実に和訳}

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

追記は `wiki-append.py` だけ。対象は `## 未編纂の観察` と `## 未解決の問い`。**主題節を書き換えない。** 問い節には問いか未解決点だけを書き、出典付きの断定は受信箱へ書く。規約外の節(`## 未解決の問い（追記）` など)を作らない。**単一ソース事実は受信箱に書かない。** 単一論文で閉じる用語は source に留める。**新規最大 3 / 更新最大 5。** 溢れたら log 用に `Deferred:` を報告する。resolve が `NONE` かつ `dialect-gate:` を返した候補は新設しない(観察は source に留め、`concept-candidates.py add --reason "dialect-gate"` で台帳に積む)。`ledger:` が `ready` の候補を新設してよいのは、`dialect-gate:` が無く `is_create_blocked` でもないときだけである。**ハブ concept は子へ書く**(ハブへは子へのリンクと子をまたぐ観察だけ)。矛盾は `> [!contradiction]`。`- 概念:` は下のテーマ塊手順に従う。

## 数値の照合

観察と source ページに、出典に無い数値や帰属の取り違えが混じる(32 GPU と書いたが原文は 64 GPU、節番号の取り違え)。書いた数値は記憶ではなく抽出テキストから写し、書いたあと機械照合する。

```bash
# source ページを書いた直後。本文と key_claims の数値を自分の raw 抽出テキストに照らす
python3 scripts/claim-audit.py numbers --source-page "wiki/sources/@YYYY__SOURCE__Title.md"
# concept への追記の前。append の spec を先にファイルへ書き、観察と問いの数値を、その行が引く出典に照らす
python3 scripts/claim-audit.py numbers --spec "$TMPDIR/appends-<slug>.json"
```

- 終了 1 は `missing`(ページにも raw にも無い数値)がある印。1 件ずつ `rg -n -C2 '<数値か英語の語>' ".raw/papers/<slug>.txt" | head -20` で原文を引き、原文の値に直すか、その数値を消す。PDF は開かない。txt を全文 Read しない。
- 観察が既存 source B の数値を述べるなら、その行に B の wikilink が要る(照合は行が引く出典だけに照らす)。B の raw も同じく `rg` で確かめる。
- 和・比・差のように自分で導いた値は、導出と分かる書き方にする(「34.72% と 24.72% の和で 59.44%」)。機械照合は導出値を拾えないので、報告に件数を書く。
- `caption_missing` は図から読んだ数値で、抽出テキストに無いのが普通である。図を読み直すためだけに画像を Read しない。
- 節番号・章番号・帰属(誰が・どの手法が・どの規模で)は機械照合が弱い。書くときに `rg -n '^ *<N.M> ' ".raw/papers/<slug>.txt"` で節見出しを確かめてから書く。
- 完了報告に `numbers: source missing=N→0 / spec missing=M→0(残りは導出値 K)` を書く。

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

未構築のまま / 既存 concept 0 / 全て `on_backbone: false` / 接するハブ 0 なら、resolve 結果だけを related にする。JSON を開けて補完してはならない。

## 触ってはいけないファイル

`wiki/index.md`、各 `_index.md`、`hot.md`、`log.md`、`overview.md`、`.raw/.manifest.json`。**catalog を subagent が書かない。** **`papers/` `research/` `structures/` `notes/` を書き換えない。**

## 文献候補

source を書いたあと、catalog の前(このセッションでは catalog は書かない)にこの順で呼ぶ。

```bash
python3 scripts/paper-refs.py extract --source "@YYYY__SOURCE__Title"
python3 scripts/paper-refs.py mark-ingested --page "wiki/sources/@YYYY__SOURCE__Title.md"
```

| 終了 | 振る舞い |
|---|---|
| 0 | 完了報告に `Ref-candidates: mentions=+N ready=M`(stdout の `mentions_added` / `ready`) |
| 3 | `Ref-candidates: skipped missing` で続行 |
| 75 | 2 秒待って同じコマンドを 1 回だけ再試行。なお 75 なら `Ref-candidates: skipped lock` で続行 |
| 1 | 壊れた台帳。ingest を止めて報告。空台帳で上書きしない |
| 2 | 呼び間違い。止める |

`Deferred:` に文献 ID も題名も書かない。候補を ingest しない。`extract` は本文の `[N]` から役割も書く。対比は直前窓の定型句である。ready は変えない。

## verify

```bash
python3 scripts/wiki-verify-ingest.py --pages "wiki/sources/@YYYY__SOURCE__Title.md" \
  --attachments-slug <slug> --figure-ids-from ".raw/papers/<slug>.txt"
```

`FIG-MISSING` は `scripts/crop-paper-figures.py` で足す。ページ全幅の手クリップはしない。
## git

`git add` / `git commit` / `git stash` / `git checkout` 禁止。確認の `git status` / `git diff` のみ。

## 完了報告

作成・更新したページ(すべて) / 一行の key insight / 気づいた矛盾 / 図表の総数と除外理由 / Deferred / `contact-sheet: N sheets / individual-reads: K`(K はシートを除く画像 Read の実数) / `numbers: source missing=N→0 / spec missing=M→0(残りは導出値 K)`。「これから〜する」で終えない。
