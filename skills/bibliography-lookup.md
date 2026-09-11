# 書誌照会(識別子優先)

`wiki-gap` / `wiki-ideate` が共有する外側照合の手順。Semantic Scholar Graph API は使わない。キーも 1Password も要らない。

## 規律

- 外部へ出してよいのは**題名と識別子だけ**。ノート本文、vault のパス、`z99_private/` の内容は送らない。
- 検索語は concept 名と論文題名だけ。文や着想の説明をクエリにしない。
- 通信はサンドボックス外(escalated)で行う。ホストが遮断されたら回避せず、権限の確認を出す。
- `timeout` コマンドは macOS に無いので使わない。
- 識別子が取れなければ捏造しない。未検証のまま残すか、`metadata_failed` にする。
- 被引用数は正典性の根拠にしない。会場と、サーベイかどうかで判断する。

## 経路(この順)

| 手元にあるもの | 先 | 次 | 取れるもの |
|---|---|---|---|
| arXiv ID(`YYMM.NNNNN` または `arxiv.org/abs/` / `pdf/`) | arXiv API | DBLP で会場を足す | 題・年・要旨・DOI |
| DOI | Crossref | プレプリント ID があれば arXiv | 題・年・会場。要旨はしばしば空 |
| 計算機科学の題名だけ | arXiv 題名検索 | DBLP は JSON が返ったときだけ会場を足す | 要旨は arXiv。会場は DBLP か会議名 |
| Semantic Scholar メールの URL | メール本文の題名と、リンク先の arXiv / DOI | 上のいずれかに落とす | Graph で paperId を引き直さない |
| 会議の採択リスト | 会議名を会場として採用 | 題名で DBLP / arXiv | `publicationTypes` を API に聞かない |

OpenAlex は使わない。2026 年から鍵必須・検索は従量であり、短い移行の逃げ道にならない。

## パーセントエンコード

```bash
urlencode() {
  perl -MURI::Escape -e 'print uri_escape($ARGV[0], "^A-Za-z0-9\-._~")' "$1" 2>/dev/null \
    || perl -e 'my $s=$ARGV[0]; $s=~s/([^A-Za-z0-9\-._~])/sprintf("%%%02X",ord($1))/ge; print $s' "$1"
}
```

## 呼び出し

間隔は供給源ごと。**並列に叩かない**。バッチの前に 1 件だけ疎通を見る。

```bash
# arXiv: 識別子。3 秒間隔。
curl -sS -m 30 "https://export.arxiv.org/api/query?id_list=${ARXIV_ID}"

# arXiv: 題名。新規性検索は all: に concept 名を載せる。
curl -sS -m 30 "https://export.arxiv.org/api/query?search_query=ti:$(urlencode "$TITLE")&max_results=5"
curl -sS -m 30 "https://export.arxiv.org/api/query?search_query=all:$(urlencode "$CONCEPT_A $CONCEPT_B")&max_results=10"

# DBLP: 題名解決は title:、新規性検索は語を並べる。2 秒間隔。
# 2026-09 時点でミラーがボット判定の HTML(200) を返すことがある。先頭が `{` でなければその供給源を捨て、再試行しない。
curl -sS -m 30 -A "research-vault/bibliography-lookup (mailto:research-vault@users.noreply.github.com)" \
  "https://dblp.org/search/publ/api?q=title:$(urlencode "$TITLE")&format=json&h=5"
curl -sS -m 30 -A "research-vault/bibliography-lookup (mailto:research-vault@users.noreply.github.com)" \
  "https://dblp.org/search/publ/api?q=$(urlencode "$CONCEPT_A $CONCEPT_B")&format=json&h=10"

# Crossref: DOI のスラッシュもエンコードする。1 秒間隔。
curl -sS -m 30 -A "research-vault/bibliography-lookup (mailto:research-vault@users.noreply.github.com)" \
  "https://api.crossref.org/works/$(urlencode "$DOI")"
```

少数件の存在確認は、API の代わりにランディングページ(`https://arxiv.org/abs/<id>` または `https://doi.org/<doi>`)を取ってもよい。

## 応答から拾う欄

- arXiv(Atom): `title` / `published`(年) / `id`(arXiv URL) / `summary`(要旨) / `arxiv:doi`
- DBLP(JSON `result.hits.hit[].info`): `title` / `year` / `venue` / `doi` / `url` / `key`
- Crossref(`message`): `title[0]` / `issued` / `container-title[0]` / `event.name` / `DOI` / `abstract`(無いことが多い)

題の一致は正規化して見る(小文字、連続空白、末尾の句読点を落とす)。先頭ヒットを無検証で採らない。

DBLP の応答が JSON でない(ボット判定の HTML を 200 で返す)ときは、その供給源を捨てて arXiv / Crossref / ランディングページだけで進める。同じ URL を連打しない。

## 使わないもの

- Semantic Scholar Graph API(キー無しは 429、キー失効は 403)
- OpenAlex を主経路にすること
- 訓練データの記憶だけで識別子や年を埋めること
