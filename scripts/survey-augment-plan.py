#!/usr/bin/env python3
"""survey-augment-plan.py — wiki-survey「一括増補」の決定的な前処理。

増補の中身(どの章へ何を書くか)は決めない。次の 4 つだけを機械的に出す。

  (a) 各サーベイの実質的な最終増補日。wiki/log.md と wiki/log/*.md の見出し
      `## [YYYY-MM-DD] wiki-survey | <ページ>` と `## [YYYY-MM-DD] query* | <ページ>` から取る。
      見取り図・図表転載・整文のような本文の source を増やさない保守エントリは除く。
      frontmatter の updated は一括保守で書き換わるので使わない。
  (b) 最終増補日より後に created され、まだ収録していない source。
  (c) source とサーベイの関連度と、「新 source → 候補サーベイ」の逆引き表。
  (d) 2 ページ以上の候補になった共有 source。

関連度はリンク構造だけで計算する(retrieve.py は呼ばない。全ページ分を回すと遅く、
新 source が索引に未登録だと何も返さない)。source の特徴は、その source を
sources に挙げる concept / entity、source 自身の related、主題タグである。
サーベイ側の重みは、既収録 source のうちその特徴を持つ割合と、サーベイ自身の related である。

出力:
  --out-dir(既定 .vault-meta/survey-augment/)に plan.json と plan.md を書く。
  stdout には plan.md と同じ要約を出す。--json なら stdout は JSON。

Usage:
  python3 scripts/survey-augment-plan.py
  python3 scripts/survey-augment-plan.py --min-score 0.08 --top-k 3
  python3 scripts/survey-augment-plan.py --since 2026-10-01 --json --no-write

Exit codes:
  0 — 成功
  2 — 使い方の誤り
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_USAGE = 2

DEFAULT_MIN_SCORE = 0.08
DEFAULT_TOP_K = 3
DEFAULT_OUT_DIR = ".vault-meta/survey-augment"

LOG_HEADING_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s*([^|\n]+?)\s*\|\s*(.+?)\s*$")
# 見出しの種別。wiki-survey と query / query-deep / query(deep) などを採る。
AUGMENT_KIND_RE = re.compile(r"^(wiki-survey|query)(?![-\w]*recommend)", re.I)
# 本文の source を増やさない保守エントリ。見出しのどこかに含まれていれば除く。
MAINTENANCE_RE = re.compile(r"見取り図|図表転載|図の転載|図表の転載|書籍図の転載|図表差し替え|整文|全サーベイ")
TRAILING_PAREN_RE = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")
DATE_TAG_RE = re.compile(r"^\d{4}[/-]\d{2}([/-]\d{2})?$")
GENERIC_TAGS = frozenset({"source", "paper", "book", "slides", "video", "thesis", "survey", "chapter"})


def vault_root():
    return Path(os.environ.get("WIKI_VAULT_ROOT") or _SCRIPTS.parent).resolve()


def _stats_mod():
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_STATS = _stats_mod()
split_frontmatter = _STATS.split_frontmatter
as_list = _STATS.as_list
wikilink_target = _STATS.wikilink_target

BODY_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


# ---------------------------------------------------------------- 名前の正規化

def norm_name(s):
    """ログ見出しとページ名の照合キー。空白・記号・「の」を落とす。"""
    s = str(s).strip().strip('"').strip("'")
    s = re.sub(r"[\s/／・\-–—―_]+", "", s)
    return s.replace("の", "").lower()


def link_stem(target):
    return Path(str(target).strip()).name.removesuffix(".md")


def parse_day(value):
    if value is None:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value).strip().strip('"'))
    return m.group(1) if m else None


def list_md(dirpath):
    if not dirpath.is_dir():
        return []
    return sorted(p for p in dirpath.rglob("*.md") if p.is_file() and not p.name.startswith("_"))


def read_page(path):
    try:
        return split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}, ""


# ---------------------------------------------------------------- (a) 最終増補日

def scan_surveys(root):
    rows = []
    for path in list_md(root / "wiki" / "surveys"):
        fm, body = read_page(path)
        if str(fm.get("type", "survey")).strip() != "survey":
            continue
        title = str(fm.get("title") or path.stem).strip()
        names = [path.stem, title]
        names += [wikilink_target(a) or str(a) for a in as_list(fm.get("aliases"))]
        included = {link_stem(t) for t in (wikilink_target(s) for s in as_list(fm.get("sources"))) if t}
        included |= {link_stem(m) for m in BODY_LINK_RE.findall(body)}
        related = {link_stem(t) for t in (wikilink_target(r) for r in as_list(fm.get("related"))) if t}
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "stem": path.stem,
            "title": title,
            "keys": {norm_name(n) for n in names if str(n).strip()},
            "created": parse_day(fm.get("created")),
            "included": included,
            "frontmatter_sources": [link_stem(wikilink_target(s)) for s in as_list(fm.get("sources")) if wikilink_target(s)],
            "related": related,
        })
    return rows


def log_files(root):
    files = [root / "wiki" / "log.md"]
    files += sorted((root / "wiki" / "log").glob("*.md"))
    return [f for f in files if f.is_file()]


def parse_log_headings(text):
    """(日付, 種別, 見出しの対象部分) を返す。"""
    out = []
    for line in text.splitlines():
        m = LOG_HEADING_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2).strip(), m.group(3).strip()))
    return out


def heading_page_key(target):
    """見出しの対象部分から末尾の括弧書き(増補内容の注記)を落として照合キーにする。"""
    t = target
    while True:
        stripped = TRAILING_PAREN_RE.sub("", t)
        if stripped == t:
            break
        t = stripped
    return norm_name(t)


def last_augment_dates(root, surveys):
    """サーベイの path → {date, heading} と、照合できなかった見出しの一覧。"""
    by_key = {}
    for s in surveys:
        for k in s["keys"]:
            by_key.setdefault(k, set()).add(s["path"])
    last = {}
    unmatched = []
    for f in log_files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for day, kind, target in parse_log_headings(text):
            if not AUGMENT_KIND_RE.match(kind):
                continue
            heading = f"[{day}] {kind} | {target}"
            if MAINTENANCE_RE.search(target):
                continue
            hits = by_key.get(heading_page_key(target), set())
            if len(hits) != 1:
                if kind.lower().startswith("wiki-survey"):
                    unmatched.append(heading)
                continue
            path = next(iter(hits))
            if path not in last or day > last[path]["date"]:
                last[path] = {"date": day, "heading": heading}
    return last, unmatched


# ---------------------------------------------------------------- (b) 新 source

def scan_sources(root):
    rows = {}
    for path in list_md(root / "wiki" / "sources"):
        fm, _ = read_page(path)
        if str(fm.get("type", "")).strip() != "source":
            continue
        tags = set()
        for t in as_list(fm.get("tags")):
            t = str(t).strip().strip('"').lower()
            if t and not DATE_TAG_RE.match(t) and t not in GENERIC_TAGS:
                tags.add("tag:" + t)
        related = {"page:" + link_stem(t) for t in (wikilink_target(r) for r in as_list(fm.get("related"))) if t}
        rows[path.stem] = {
            "stem": path.stem,
            "path": path.relative_to(root).as_posix(),
            "title": str(fm.get("title") or path.stem).strip(),
            "address": str(fm.get("address") or "").strip(),
            "created": parse_day(fm.get("created")),
            "features": tags | related,
        }
    return rows


def attach_backlinks(root, sources):
    """concept / entity の sources に挙がっていれば、その page を source の特徴に足す。"""
    for sub in ("concepts", "entities"):
        for path in list_md(root / "wiki" / sub):
            fm, _ = read_page(path)
            for item in as_list(fm.get("sources")):
                t = wikilink_target(item)
                if not t:
                    continue
                src = sources.get(link_stem(t))
                if src is not None:
                    src["features"].add("page:" + path.stem)


# ---------------------------------------------------------------- (c) 関連度

def idf_table(sources):
    n = max(len(sources), 1)
    df = {}
    for s in sources.values():
        for f in s["features"]:
            df[f] = df.get(f, 0) + 1
    return {f: math.log(1 + n / c) for f, c in df.items()}


def survey_profile(survey, sources):
    """特徴 → 重み(0..1)。既収録 source の保有率と、サーベイ自身の related。"""
    members = [sources[s] for s in survey["frontmatter_sources"] if s in sources]
    counts = {}
    for m in members:
        for f in m["features"]:
            counts[f] = counts.get(f, 0) + 1
    n = max(len(members), 1)
    prof = {f: c / n for f, c in counts.items()}
    for r in survey["related"]:
        prof["page:" + r] = max(prof.get("page:" + r, 0.0), 1.0)
    return prof


def relevance(src, profile, idf):
    """新 source の特徴のうちサーベイ側にもあるものの重み付き割合。0..1。"""
    feats = src["features"]
    if not feats:
        return 0.0, []
    total = sum(idf.get(f, 0.0) for f in feats)
    if total <= 0:
        return 0.0, []
    hits = [(idf.get(f, 0.0) * profile[f], f) for f in feats if f in profile]
    score = sum(h for h, _ in hits) / total
    top = [f for _, f in sorted(hits, reverse=True)[:4]]
    return score, top


# ---------------------------------------------------------------- 索引の点検

def indexed_addresses(root):
    """BM25 索引に入っているページの address 集合。cid は `<address>:<chunk>` 形式。索引が無ければ None。"""
    db = root / ".vault-meta" / "bm25" / "index.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {str(r[0]).split(":", 1)[0] for r in con.execute("SELECT DISTINCT cid FROM docs")}
        finally:
            con.close()
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------- 組み立て

def build_plan(root=None, *, min_score=DEFAULT_MIN_SCORE, top_k=DEFAULT_TOP_K, since=None,
               include_same_day=False, check_index=True):
    root = Path(root) if root else vault_root()
    surveys = scan_surveys(root)
    sources = scan_sources(root)
    attach_backlinks(root, sources)
    idf = idf_table(sources)
    last, unmatched = last_augment_dates(root, surveys)

    pages = []
    reverse = {}
    for s in surveys:
        entry = last.get(s["path"])
        cutoff = since or (entry["date"] if entry else s["created"])
        profile = survey_profile(s, sources)
        cands = []
        for src in sources.values():
            if not src["created"] or not cutoff:
                continue
            newer = src["created"] >= cutoff if include_same_day else src["created"] > cutoff
            if not newer or src["stem"] in s["included"]:
                continue
            score, shared = relevance(src, profile, idf)
            if score >= min_score:
                cands.append({"source": src["stem"], "score": round(score, 4), "shared": shared})
        cands.sort(key=lambda c: (-c["score"], c["source"]))
        pages.append({
            "path": s["path"],
            "title": s["title"],
            "last_augment": entry["date"] if entry else None,
            "last_augment_heading": entry["heading"] if entry else None,
            "cutoff": cutoff,
            "cutoff_basis": "since" if since else ("log" if entry else "created"),
            "candidates": cands,
        })
        for c in cands:
            reverse.setdefault(c["source"], []).append({"survey": s["path"], "score": c["score"]})

    # 逆引きは source ごとに上位 top_k へ絞り、絞った結果をページ側の候補にも反映する。
    kept = set()
    for stem, lst in reverse.items():
        lst.sort(key=lambda r: (-r["score"], r["survey"]))
        del lst[top_k:]
        kept |= {(stem, r["survey"]) for r in lst}
    for p in pages:
        p["candidates"] = [c for c in p["candidates"] if (c["source"], p["path"]) in kept]
        p["action"] = "augment" if p["candidates"] else "skip"

    reverse_rows = []
    for stem in sorted(reverse, key=lambda k: (-len(reverse[k]), k)):
        src = sources[stem]
        reverse_rows.append({
            "source": stem,
            "path": src["path"],
            "address": src["address"],
            "created": src["created"],
            "surveys": reverse[stem],
        })
    shared = [r for r in reverse_rows if len(r["surveys"]) >= 2]

    index_missing = None
    if check_index:
        found = indexed_addresses(root)
        if found is not None:
            index_missing = sorted(r["source"] for r in reverse_rows if r["address"] and r["address"] not in found)

    return {
        "generated": date.today().isoformat(),
        "params": {"min_score": min_score, "top_k": top_k, "since": since, "include_same_day": include_same_day},
        "pages": pages,
        "reverse": reverse_rows,
        "shared": shared,
        "index_missing": index_missing,
        "unmatched_log_headings": unmatched,
    }


def render_summary(plan):
    pages = plan["pages"]
    todo = [p for p in pages if p["action"] == "augment"]
    lines = [f"# 一括増補の計画 {plan['generated']}", ""]
    lines.append(f"- 対象 {len(todo)} / {len(pages)} ページ、新 source {len(plan['reverse'])} 本、共有 source {len(plan['shared'])} 本")
    if plan["index_missing"] is None:
        lines.append("- 索引の点検: 未実施(index.sqlite が無い)")
    elif plan["index_missing"]:
        lines.append(f"- 索引に未登録の source {len(plan['index_missing'])} 本 → 先に `python3 scripts/wiki-retrieve-refresh.py --pages <path...>`")
    else:
        lines.append("- 索引の点検: 新 source はすべて登録済み")
    lines += ["", "## ページ別", "", "| ページ | 最終増補 | 基準 | 候補 |", "|---|---|---|---|"]
    for p in sorted(pages, key=lambda p: (-len(p["candidates"]), p["title"])):
        lines.append(f"| {Path(p['path']).stem} | {p['last_augment'] or '-'} | {p['cutoff_basis']} | {len(p['candidates'])} |")
    if plan["shared"]:
        lines += ["", "## 共有 source(主張カードを 1 回だけ作る)", ""]
        for r in plan["shared"]:
            targets = ", ".join(f"{Path(s['survey']).stem}({s['score']:.2f})" for s in r["surveys"])
            lines.append(f"- {r['source']} → {targets}")
    lines += ["", "## ページ別の候補", ""]
    for p in todo:
        lines.append(f"### {Path(p['path']).stem}")
        for c in p["candidates"]:
            lines.append(f"- {c['source']}({c['score']:.2f})")
        lines.append("")
    if plan["unmatched_log_headings"]:
        lines += ["## 照合できなかった wiki-survey 見出し", ""]
        lines += [f"- {h}" for h in plan["unmatched_log_headings"]]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="候補に残す関連度の下限")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="1 source あたりの候補サーベイ数の上限")
    ap.add_argument("--since", help="全ページ共通の基準日 YYYY-MM-DD(ログの最終増補日を無視する)")
    ap.add_argument("--include-same-day", action="store_true", help="基準日当日に created された source も含める")
    ap.add_argument("--no-index-check", action="store_true", help="BM25 索引の登録点検を省く")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="plan.json と plan.md の書き出し先(vault 相対)")
    ap.add_argument("--no-write", action="store_true", help="ファイルを書かない")
    ap.add_argument("--json", action="store_true", help="stdout を JSON にする")
    args = ap.parse_args(argv)
    if args.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
        print("--since は YYYY-MM-DD", file=sys.stderr)
        return EXIT_USAGE
    if args.top_k < 1:
        print("--top-k は 1 以上", file=sys.stderr)
        return EXIT_USAGE

    root = vault_root()
    plan = build_plan(root, min_score=args.min_score, top_k=args.top_k, since=args.since,
                      include_same_day=args.include_same_day, check_index=not args.no_index_check)
    summary = render_summary(plan)
    if not args.no_write:
        out = root / args.out_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "plan.md").write_text(summary, encoding="utf-8")
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(summary, end="")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
