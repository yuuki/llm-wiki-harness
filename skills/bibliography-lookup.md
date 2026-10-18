# 書誌照会(識別子優先)

`wiki-gap` / `wiki-ideate` が共有する外側照合の手順。API キーの要らない経路だけを使う。

## 規律

- 外部へ出してよいのは**題名と識別子だけ**。ノート本文、vault のパス、`z99_private/` の内容は送らない。
- 検索語は concept 名と論文題名だけ。文や着想の説明をクエリにしない。
- 外部への通信にはネットワーク権限が要る。ホストが遮断されたら回避せず、権限の確認を出す。
- `timeout` コマンドは macOS に無いので使わない。
- 識別子が取れなければ捏造しない。未検証のまま残すか、`metadata_failed` にする。
- 被引用数は正典性の根拠にしない。会場と、サーベイかどうかで判断する。

## 経路(この順)

| 手元にあるもの | 先 | 次 | 取れるもの |
|---|---|---|---|
| arXiv ID(`YYMM.NNNNN` または `arxiv.org/abs/` / `pdf/`) | arXiv API | DBLP で会場を足す | 題・年・要旨・DOI |
| DOI | Crossref | プレプリント ID があれば arXiv | 題・年・会場。要旨はしばしば空 |
| 計算機科学の題名だけ | arXiv 題名検索 | DBLP は JSON が返ったときだけ会場を足す | 要旨は arXiv。会場は DBLP か会議名 |
| Semantic Scholar メールの URL | メール本文の題名と、リンク先の arXiv / DOI | 上のいずれかに落とす | 落とした先の経路と同じ |
| 会議の採択リスト | 会議名を会場として採用 | 題名で DBLP / arXiv | 会場は採択リストから。題・識別子は DBLP / arXiv |

OpenAlex は**補助経路**として使える(キー無しでは 1 日に少額の無料枠があり、単発の `/works/doi:` は無料、フィルタと `search=` は従量で数える)。呼び出しは `scripts/bib-lookup.py` に集約してあり、残額をヘッダで見て尽きたら終了コード 4 で止まる。4 が返ったら arXiv / Crossref へ退避し、続行しない。料金体系は変わりうるので、4 以外の理由で 402 / 429 が続くなら使うのをやめる。サンドボックスでは `api.openalex.org` と `api.crossref.org` を許可ホストに足す。

| 用途 | コマンド | 備考 |
|---|---|---|
| 撤回照会(wiki-lint 19) | `bib-lookup.py retracted` | `wiki/sources/` の `doi:` を 50 件ずつ |
| 出版社版の書誌(題・年・会場・巻号頁・abstract・訂正) | `bib-lookup.py meta DOI` | Crossref。ACM は abstract が取れる。`relation` は空のことが多い |
| 出版社版 DOI → arXiv 版 | `bib-lookup.py pair DOI` | 一方向のみ。arXiv DOI で直接引くと 404 |
| 橋渡し文献(両側を引く論文) | `bib-lookup.py cites-both ID ID` | `cites:` の AND。ID は DOI か `W...` |
| 参考文献表(DOI 付きの割合) | `bib-lookup.py refs DOI` | Crossref。DOI が付かない項目も多い |
| abstract の一括取得 | `bib-lookup.py abstracts DOI...` | OpenAlex。索引済みの論文向け。採択直後は未登録が多い |
| 母集団の厚み(年ごとの件数) | `bib-lookup.py population QUERY` | `group_by`。`has_abstract` の集計は当てにならない |
| 題名・語での検索 | `bib-lookup.py search QUERY` | arXiv に無い論文誌・会議論文も拾う |

被引用数は、ここでも正典性の根拠にしない。

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

DBLP の応答が JSON でない(ボット判定の HTML を 200 で返す)ときは、その供給源を捨てて arXiv / Crossref / ランディングページだけで進める。同じ URL を連打しない。2026-10 時点の DBLP は Anubis のチャレンジを HTTP 202 で返す。チャレンジは突破しない。

DBLP が使えないときの会場確認は Crossref の題名検索で行う。

```bash
curl -sS -m 30 -A "research-vault/bibliography-lookup (mailto:research-vault@users.noreply.github.com)" \
  "https://api.crossref.org/works?query.bibliographic=$(urlencode "$TITLE")&rows=5&select=DOI,title,subtitle,type,container-title,event,issued"
```

先頭ヒットを無検証で採らず、`type` が proceedings-article / journal-article で題名が一致するものだけを採る。ACM は題名と副題を別欄に登録する(`title: ["Sieve"]`、`subtitle: ["Actionable Insights from ..."]`)ので、`title: subtitle` とつないでから照合する。

## 使わないもの

- Semantic Scholar Graph API(キー無しは 429、キー失効は 403)
- OpenAlex を唯一の経路にすること(無料枠が尽きる。arXiv / Crossref に退避できる形で使う)
- 訓練データの記憶だけで識別子や年を埋めること
