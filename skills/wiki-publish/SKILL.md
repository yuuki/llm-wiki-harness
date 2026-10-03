---
name: wiki-publish
description: "`wiki/surveys/` の教科書・サーベイページを、wiki 外部の読者向けに清書して `notes/<domain>/` に保存する。wikilink を全除去し、[N] 番号引用と URL 付きの参考文献リストに変換し、人名に所属を併記し、japanese-tech-writing 規範で整文する。Triggers: '清書して', '外部公開用の文書として保存して', 'wikiの外へ公開', '参考文献を正式なフォーマットに'. 教科書・サーベイページの作成自体(wiki-survey)や wiki 内ページの充実化には使わない。"
---

# wiki-publish: wiki 教科書ページの外部公開用清書

`wiki/surveys/` に編纂した教科書・サーベイページを、wiki の外の読者が読める独立した文書に変換して `notes/<domain>/` に保存する。`notes/` に既存の清書版があれば、そのフォーマットが仕様である。迷ったら実例を開いて倣う。

wiki 側は**一切変更しない**（非破壊）。出力は `notes/` への新規ファイルのみ。同名のファイルが既にあれば上書きせず、ユーザーに確認する（既存の `notes/` は書き換えないのが vault の鉄則である）。

## 手順

### 1. 対象と出典の解決

- 対象の `wiki/surveys/<タイトル>.md` を読む。
- 本文の `(Source: [[...]])`（半角・全角とも）を、主張と出典の対応の正本とする。survey ページは全主張にこの形で出典を付けているので、これを落とすと `[N]` をどの文に付けるかの根拠が無くなる。
- frontmatter の `sources:` は、書誌を解決する source ページの一覧として使う。各 source の書誌情報（著者・タイトル・掲載媒体・年・URL）は、source ページの frontmatter から集める。source は数十本から百本を超えるので、本文は開かない。frontmatter で足りない項目だけ、`python3 scripts/wiki-excerpt.py <path> --sections 論文情報` で補う。
- 出典に本文初出順で番号 `[1]..[N]` を割り当てる。

### 2. 本文の変換

単純置換ではなく**編集を伴う**。日本語の整文には `japanese-tech-writing` スキルを適用する（常体・一文一行・強調の抑制・中黒区切りを読点へ）。

- **frontmatter は最小限に**: `title` / `created`(今日) / `tags`(インライン配列・英語トピックタグ) のみ。wiki 用の `address` / `type` / `question` / `answer_quality` / `related` / `sources` / `status` / 日付タグは全除去。
- **本文先頭に H1 タイトル**とリード文（2行程度）。
- **見出し**: 部・章構成は維持しつつ、章内番号（`4.1` 等）は除去して無番号の `####` にする。部題の「——サブタイトル」装飾は外す。
- **wikilink は完全除去**（`[[` が 0 件になるまで）:
  - 本文の `(Source: [[...]])` → 番号引用 `[N]`（出典が複数なら `[3][7]`）
  - entity リンク `[[Will Gallego]]` → 平文 + 所属併記「Will Gallego（Etsy）」
  - 人物の初出は `人名（所属, 年）` 形式（例: 「Laura Nolan（Stanza Systems, 2022）は…を提唱した [9]」）。所属は wiki の entity ページか出典で裏が取れるものだけ書く
- **図表**: `![[...]]` の埋め込みは、標準 Markdown の画像リンク `![キャプション](<保存先からの相対パス>)` に変換する（例: `../../wiki/surveys/_attachments/<file>.png`）。図の出典行は `[N]` に変換して残す。**書籍由来（`publish: false` の source）の図と長い引用を載せるかは、変換の前にユーザーに確認する**。清書版は wiki の外へ出す文書であり、wiki 内での転載可否とは別の判断になる。
- **callout は原則除去**。補足は脚注 `[^slug]` に落とす。
- **表は保持**してよい。用語集の付録は箇条書きにする。

### 3. 付録

- `### A. 用語集` — 箇条書き。
- `### B. 参考文献` — `[N]` 全件。書式（次行に裸 URL を置く形式に統一する）:

```
[1] John Lunney, Sue Lueder. "Postmortem Culture: Learning from Failure." *Site Reliability Engineering*, O'Reilly, 2016.
https://sre.google/sre-book/postmortem-culture/
```

- wiki 内部対応表（ソースマッピング等）の付録は**削除**する。外部読者には意味がない。

### 4. 保存と検証

- 保存先: `notes/<domain>/<wiki側タイトル> - 清書版.md`（例: `notes/sre/`）。domain はテーマから選ぶ（既存の `notes/` サブディレクトリに合わせる）。
- 検証: (a) `[[` が本文に残っていない（`![[` の埋め込みも含む） (b) 本文中の `[N]` がすべて参考文献に存在し、逆も然り (c) 所属の記載が出典で裏付くこと。
- commit: `notes: add <タイトル> - 清書版`。保存したファイルだけを `git commit -m "..." -- <パス>` で渡して対象を限定する。wiki 側の index/log は更新しない（前例に従う）。

## やってはいけないこと

- wiki 側ページの編集・削除（このスキルは読み取りのみ）
- 出典で裏の取れない所属・年・数値の付加（entity ページか原典で確認できるものに限る）
- `[Author+, CONF2024]` 形式の引用識別子（清書版は `[N]` 番号形式。識別子形式はユーザーが明示した場合のみ）
