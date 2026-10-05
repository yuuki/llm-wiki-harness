#!/usr/bin/env python3
"""bib-lookup.py — OpenAlex / Crossref の書誌照会ヘルパー(キー不要の経路だけ)。

skill が書誌 API を叩くときの共通口。外へ出すのは題名・識別子・検索語だけで、
ノート本文や vault のパスは送らない。並列には叩かない(1 呼び出し 1 リクエストを直列、
間隔 1 秒)。OpenAlex は無料枠(キー無しで 1 日 $0.10)の残額をヘッダで見て、
下回ったら終了コード 4 で止まる。呼び出し側は arXiv / Crossref 経路へ退避する。

使い方:
  python3 scripts/bib-lookup.py retracted [--limit N]      # wiki/sources の doi: を撤回照会
  python3 scripts/bib-lookup.py meta DOI                   # Crossref: 題・年・会場・巻号頁・abstract
  python3 scripts/bib-lookup.py pair DOI                   # OpenAlex: 出版社版 DOI → arXiv 版の対応
  python3 scripts/bib-lookup.py cites-both ID ID [ID...]   # 全部を引く論文(ID は DOI か W...)
  python3 scripts/bib-lookup.py refs DOI                   # Crossref: 参考文献表(DOI 付きの割合)
  python3 scripts/bib-lookup.py abstracts DOI [DOI...]     # OpenAlex: abstract をまとめて取得
  python3 scripts/bib-lookup.py population QUERY [--from-year Y]  # 年ごとの件数
  python3 scripts/bib-lookup.py search QUERY [--year Y] [--limit N]

終了コード:
  0 — 成功 / 2 — 使い方 / 3 — 該当なし / 4 — 無料枠が尽きた / 5 — 通信失敗
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAILTO = os.environ.get("BIB_LOOKUP_MAILTO", "llm-wiki-harness@users.noreply.github.com")
UA = f"llm-wiki-harness/bib-lookup (mailto:{MAILTO})"
OPENALEX = "https://api.openalex.org/"
CROSSREF = "https://api.crossref.org/"
MIN_REMAINING_USD = 0.01
BATCH = 50
INTERVAL = float(os.environ.get("BIB_LOOKUP_INTERVAL", "1.0"))

EXIT_OK, EXIT_USAGE, EXIT_NONE, EXIT_BUDGET, EXIT_NET = 0, 2, 3, 4, 5

DOI_RE = re.compile(r"10\.\d{4,9}/\S+")


class BudgetExhausted(Exception):
    pass


class NotFound(Exception):
    pass


class NetError(Exception):
    pass


_last_call = [0.0]
_remaining = [None]


def http_json(url: str):
    """1 リクエスト。直列化のため前回から INTERVAL 秒あける。"""
    wait = INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        for attempt in (1, 2):  # 接続切れ・読み切れは 1 回だけやり直す(同じ URL の連打はしない)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    rem = r.headers.get("x-ratelimit-remaining-usd")
                    if rem is not None:
                        _remaining[0] = float(rem)
                    body = r.read()
                break
            except (http.client.HTTPException, ConnectionError):
                if attempt == 2:
                    raise
                time.sleep(2)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(url)
        if e.code in (402, 429):
            raise BudgetExhausted(url)
        raise NetError(f"HTTP {e.code}: {url}")
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
        raise NetError(f"{e}: {url}")
    finally:
        _last_call[0] = time.monotonic()
    if _remaining[0] is not None and _remaining[0] < MIN_REMAINING_USD:
        raise BudgetExhausted(f"remaining ${_remaining[0]}")
    try:
        return json.loads(body)
    except ValueError:
        # DBLP と同じく、ボット判定の HTML が 200 で返ることがある。捨てて再試行しない。
        raise NetError(f"not JSON: {url}")


def oa(path: str, **params):
    params["mailto"] = MAILTO
    return http_json(OPENALEX + path + "?" + urllib.parse.urlencode(params, safe=":,|/"))


def crossref(doi: str):
    return http_json(CROSSREF + "works/" + urllib.parse.quote(doi, safe=""))["message"]


def norm_doi(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s.rstrip(".,;)")


def short_id(s: str) -> str:
    return s.rsplit("/", 1)[-1]


def strip_jats(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def abstract_from_inverted(inv) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def work_row(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    return {
        "id": short_id(w.get("id", "")),
        "doi": norm_doi(w.get("doi") or ""),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "venue": src.get("display_name") or "",
        "type": w.get("type") or "",
    }


# ---- 撤回照会 ----------------------------------------------------------------

FM_DOI_RE = re.compile(r'^doi:\s*"?([^"\s]+)"?\s*$', re.M)


def vault_root() -> Path:
    return Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent)


def source_dois(root: Path) -> dict:
    """doi → source ページ(相対パス)。frontmatter の doi: だけを読む。"""
    out = {}
    for p in sorted((root / "wiki" / "sources").glob("*.md")):
        head = p.read_text(encoding="utf-8", errors="replace").split("\n---", 2)[0]
        m = FM_DOI_RE.search(head)
        if m and DOI_RE.match(m.group(1)):
            out.setdefault(norm_doi(m.group(1)), p.relative_to(root).as_posix())
    return out


def cmd_retracted(args) -> int:
    dois = source_dois(vault_root())
    keys = list(dois)[: args.limit] if args.limit else list(dois)
    hit, checked = [], 0
    for i in range(0, len(keys), BATCH):
        chunk = keys[i : i + BATCH]
        d = oa("works", filter="doi:" + "|".join(chunk) + ",is_retracted:true",
               select="id,doi,title", **{"per-page": BATCH})
        checked += len(chunk)
        hit += [work_row(w) for w in d["results"]]
    print(f"checked={checked} retracted={len(hit)}")
    for r in hit:
        print(f"RETRACTED\t{r['doi']}\t{dois.get(r['doi'], '?')}\t{r['title']}")
    return EXIT_OK


# ---- 個別照会 ----------------------------------------------------------------


def cmd_meta(args) -> int:
    m = crossref(norm_doi(args.doi))
    issued = (m.get("issued", {}).get("date-parts") or [[None]])[0][0]
    authors = ["{} {}".format(a.get("given", ""), a.get("family", "")).strip() for a in m.get("author", [])]
    rel = {k: [x.get("id") for x in v] for k, v in (m.get("relation") or {}).items()}
    print(json.dumps({
        "doi": m.get("DOI"),
        "title": (m.get("title") or [""])[0],
        "year": issued,
        "venue": (m.get("container-title") or [""])[0],
        "event": (m.get("event") or {}).get("name", ""),
        "volume": m.get("volume", ""), "issue": m.get("issue", ""), "page": m.get("page", ""),
        "publisher": m.get("publisher", ""),
        "authors": authors,
        "abstract": strip_jats(m.get("abstract", "")),
        "relation": rel,
        "updated_by": [u.get("type") for u in m.get("updated-by", [])],
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_pair(args) -> int:
    doi = norm_doi(args.doi)
    try:
        w = oa("works/doi:" + doi, select="id,doi,title,locations,ids")
    except NotFound:
        print("NOT_FOUND\t" + doi)
        return EXIT_NONE
    arxiv = ""
    for loc in w.get("locations", []):
        url = loc.get("landing_page_url") or ""
        m = re.search(r"arxiv\.org/abs/([0-9]{4}\.[0-9]{4,5})", url, re.I) or re.search(
            r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})", url, re.I)
        if m:
            arxiv = m.group(1)
            break
    print(f"doi={doi}\tarxiv_id={arxiv}\topenalex={short_id(w['id'])}")
    return EXIT_OK if arxiv else EXIT_NONE


def resolve_ids(items) -> list:
    out = []
    for it in items:
        if re.fullmatch(r"W\d+", it):
            out.append(it)
            continue
        w = oa("works/doi:" + norm_doi(it), select="id")
        out.append(short_id(w["id"]))
    return out


def cmd_cites_both(args) -> int:
    ids = resolve_ids(args.ids)
    flt = ",".join("cites:" + i for i in ids)
    d = oa("works", filter=flt, select="id,doi,title,publication_year,primary_location,type",
           **{"per-page": args.limit, "sort": "publication_year:desc"})
    print(f"count={d['meta']['count']}\tanchors={','.join(ids)}")
    for w in d["results"]:
        r = work_row(w)
        print(f"{r['year']}\t{r['venue']}\t{r['doi'] or r['id']}\t{r['title']}")
    return EXIT_OK if d["results"] else EXIT_NONE


def cmd_refs(args) -> int:
    m = crossref(norm_doi(args.doi))
    refs = m.get("reference", [])
    with_doi = [r for r in refs if r.get("DOI")]
    print(f"references={len(refs)}\twith_doi={len(with_doi)}")
    for r in with_doi:
        print(norm_doi(r["DOI"]))
    return EXIT_OK if refs else EXIT_NONE


def cmd_abstracts(args) -> int:
    keys = [norm_doi(d) for d in args.dois]
    found = 0
    for i in range(0, len(keys), BATCH):
        chunk = keys[i : i + BATCH]
        d = oa("works", filter="doi:" + "|".join(chunk),
               select="id,doi,abstract_inverted_index", **{"per-page": BATCH})
        for w in d["results"]:
            text = abstract_from_inverted(w.get("abstract_inverted_index"))
            found += bool(text)
            print(json.dumps({"doi": norm_doi(w.get("doi") or ""), "abstract": text}, ensure_ascii=False))
    print(f"# abstracts={found}/{len(keys)}", file=sys.stderr)
    return EXIT_OK if found else EXIT_NONE


def cmd_population(args) -> int:
    flt = "title_and_abstract.search:" + args.query
    if args.from_year:
        flt += f",publication_year:>{args.from_year - 1}"
    d = oa("works", filter=flt, group_by="publication_year")
    rows = sorted(((g["key"], g["count"]) for g in d["group_by"]), reverse=True)
    print(f"query={args.query!r}\ttotal={sum(c for _, c in rows)}")
    for y, c in rows:
        print(f"{y}\t{c}")
    return EXIT_OK if rows else EXIT_NONE


def cmd_search(args) -> int:
    flt = f"publication_year:{args.year}" if args.year else None
    p = {"search": args.query, "select": "id,doi,title,publication_year,primary_location,type",
         "per-page": args.limit}
    if flt:
        p["filter"] = flt
    d = oa("works", **p)
    for w in d["results"]:
        r = work_row(w)
        print(f"{r['year']}\t{r['type']}\t{r['venue']}\t{r['doi'] or r['id']}\t{r['title']}")
    return EXIT_OK if d["results"] else EXIT_NONE


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("retracted"); s.add_argument("--limit", type=int, default=0); s.set_defaults(fn=cmd_retracted)
    s = sub.add_parser("meta"); s.add_argument("doi"); s.set_defaults(fn=cmd_meta)
    s = sub.add_parser("pair"); s.add_argument("doi"); s.set_defaults(fn=cmd_pair)
    s = sub.add_parser("cites-both"); s.add_argument("ids", nargs="+")
    s.add_argument("--limit", type=int, default=25); s.set_defaults(fn=cmd_cites_both)
    s = sub.add_parser("refs"); s.add_argument("doi"); s.set_defaults(fn=cmd_refs)
    s = sub.add_parser("abstracts"); s.add_argument("dois", nargs="+"); s.set_defaults(fn=cmd_abstracts)
    s = sub.add_parser("population"); s.add_argument("query")
    s.add_argument("--from-year", type=int, default=0); s.set_defaults(fn=cmd_population)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--year", type=int, default=0)
    s.add_argument("--limit", type=int, default=10); s.set_defaults(fn=cmd_search)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "cites-both" and len(args.ids) < 2:
        print("cites-both は 2 件以上の ID が要る", file=sys.stderr)
        return EXIT_USAGE
    try:
        return args.fn(args)
    except NotFound as e:
        print(f"NOT_FOUND\t{e}", file=sys.stderr)
        return EXIT_NONE
    except BudgetExhausted as e:
        print(f"BUDGET\t{e}\tOpenAlex の無料枠が尽きた。arXiv / Crossref 経路へ退避する", file=sys.stderr)
        return EXIT_BUDGET
    except NetError as e:
        print(f"NETWORK\t{e}", file=sys.stderr)
        return EXIT_NET


if __name__ == "__main__":
    sys.exit(main())
