#!/usr/bin/env python3
"""wiki-resolve.py — name resolution without reading catalog pages.

Discovers existing wiki source/entity/concept pages by filename, alias, and
optional BM25. Never reads wiki/index.md or any _index.md.

Usage:
  wiki-resolve.py "NAME" [--type source|entity|concept|any] [--top 8] [--bm25]
  wiki-resolve.py entity:"Hellerstein" concept:"RDMA" source:"Megatron" --compact
  wiki-resolve.py --names-file /tmp/names.txt --compact

Resolve every name of one ingest in ONE call: the directory listing and the
alias scan are built once per run and shared by all queries, and one call costs
one resident-context read instead of N.

Per-query type override: a query may carry an `entity:` / `concept:` /
`source:` / `any:` prefix (lowercase, exact); it wins over --type. Any other
`X:` text is part of the query itself (e.g. "Re: Zero").

Stdout:
  1 query  — JSON {query, type, candidates:[...]}  (unchanged)
  N queries — JSON {results:[{query, type, candidates:[...]}, ...]}
  --compact    — one TSV line per query:
                 <query>\\t<type>\\t<HIT|NONE>\\t<path>(<match> <score>); ...
                 paths are relative to wiki/; default --top drops to 3
  --paths-only — `# <query>` header line, then one vault-relative path per line
Empty candidates is success (exit 0).

Exit codes:
  0 — success
  2 — usage error
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
SCRIPT_DIR = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_USAGE = 2

TYPE_DIRS = {
    "entity": ("wiki/entities",),
    "concept": ("wiki/concepts",),
    "source": ("wiki/sources",),
    "any": ("wiki/entities", "wiki/concepts", "wiki/sources"),
}
TYPE_FROM_DIR = {
    "wiki/entities": "entity",
    "wiki/concepts": "concept",
    "wiki/sources": "source",
}
MATCH_RANK = {"filename": 0, "alias": 1, "substring": 2, "bm25": 3}
INDEX_NAMES = frozenset({"index.md", "_index.md"})
ONE_LINER_BYTES = 2048
FM_READ_BYTES = 8192
QUERY_TYPE_PREFIXES = ("entity", "concept", "source", "any")
DEFAULT_TOP = 8
COMPACT_TOP = 3
WIKI_PREFIX = "wiki/"

QUIET = False
# Per-run caches: one directory listing and one alias scan serve every query.
_LISTDIR_CACHE = {}
_FOLDED_CACHE = {}


def log(msg):
    if QUIET:
        return
    print(msg, file=sys.stderr)


def err(msg):
    """Usage/failure line; printed even under --quiet."""
    print(msg, file=sys.stderr)


def reset_caches():
    _LISTDIR_CACHE.clear()
    _FOLDED_CACHE.clear()


def unquote(value):
    """Strip one matching pair of quotes from both ends."""
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].replace("\\'", "'").replace('\\"', '"')
    return s


def normalize_query_raw(raw):
    """Strip, unquote the line, then unquote a name after entity:/concept:/source:/any:."""
    s = unquote(raw.lstrip("\ufeff"))
    for prefix in QUERY_TYPE_PREFIXES:
        head = prefix + ":"
        if s.startswith(head):
            return head + unquote(s[len(head):])
    return s


def split_query_type(raw, default_type):
    """Split `entity:Name` into ("Name", "entity"); other `X:` stays in the query."""
    raw = raw.strip()
    for prefix in QUERY_TYPE_PREFIXES:
        head = prefix + ":"
        if raw.startswith(head):
            return raw[len(head):].strip(), prefix
    return raw, default_type


def parse_simple_yaml(text):
    data = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^(\s+)-\s+(.*)$", line)
        if item and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(unquote(item.group(2)))
            continue
        kv = re.match(r"^([A-Za-z0-9_/-]+):\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1)
        raw = kv.group(2).strip()
        if raw in ("", "|", ">"):
            data[key] = []
        elif raw == "[]":
            data[key] = []
        else:
            data[key] = unquote(raw)
    return data


def split_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            fm = parse_simple_yaml("".join(lines[1:i]))
            body = "".join(lines[i + 1:])
            return fm, body
    # 2KB prefix often ends inside a long frontmatter; parse keys, no body.
    return parse_simple_yaml("".join(lines[1:])), ""


def vault_rel(path):
    return Path(path).resolve().relative_to(VAULT_ROOT.resolve()).as_posix()


def is_index_path(rel):
    name = Path(rel).name
    return name in INDEX_NAMES or rel in ("wiki/index.md",)


def page_type_from_rel(rel):
    for prefix, typ in TYPE_FROM_DIR.items():
        if rel.startswith(prefix + "/"):
            return typ
    return "any"


def type_dirs(type_key):
    return TYPE_DIRS.get(type_key, TYPE_DIRS["any"])


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)]


def read_prefix(path, nbytes):
    """First nbytes characters; nbytes=-1 reads the whole page."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(nbytes)
    except OSError:
        return ""


def one_liner_from_prefix(text, title):
    fm, body = split_frontmatter(text)
    role = fm.get("role")
    if role and not isinstance(role, list) and str(role).strip():
        return str(role).strip().replace("\n", " ")
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s == "---":
            continue
        return s
    if fm.get("title") and not isinstance(fm.get("title"), list):
        return str(fm["title"]).strip()
    return title


def aliases_from_prefix(text):
    fm, _ = split_frontmatter(text)
    return as_list(fm.get("aliases"))


def title_from_prefix(text, fallback):
    fm, _ = split_frontmatter(text)
    title = fm.get("title")
    if title and not isinstance(title, list) and str(title).strip():
        return str(title).strip()
    return fallback


def alias_hits(query, aliases):
    q = query.casefold()
    for alias in aliases:
        a = str(alias).casefold()
        if q == a or q in a or a in q:
            return True
    return False


def better(new, old):
    nr = MATCH_RANK.get(new["match"], 99)
    or_ = MATCH_RANK.get(old["match"], 99)
    if nr != or_:
        return nr < or_
    return float(new.get("score", 0)) > float(old.get("score", 0))


def add_candidate(store, path, match, score, title=None, aliases=None):
    try:
        rel = vault_rel(path)
    except ValueError:
        return
    if is_index_path(rel):
        return
    if not Path(path).is_file():
        return
    rec = {
        "path": rel,
        "title": title or Path(rel).stem,
        "type": page_type_from_rel(rel),
        "match": match,
        "score": float(score),
        "aliases": list(aliases or []),
        "one_liner": "",
    }
    old = store.get(rel)
    if old is None:
        store[rel] = rec
        return
    if better(rec, old):
        rec["aliases"] = rec["aliases"] or old["aliases"]
        rec["title"] = rec["title"] or old["title"]
        store[rel] = rec
    else:
        if not old["aliases"] and rec["aliases"]:
            old["aliases"] = rec["aliases"]
        if rec["title"] and old["title"] == Path(old["path"]).stem:
            old["title"] = rec["title"]


def scan_dir_md(rel_dir):
    """The one real directory walk. Cached by listdir_md, so once per run."""
    abs_dir = VAULT_ROOT / rel_dir
    if not abs_dir.is_dir():
        return []
    try:
        names = os.listdir(abs_dir)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".md") or name in INDEX_NAMES:
            continue
        out.append(name)
    return out


def listdir_md(rel_dir):
    if rel_dir not in _LISTDIR_CACHE:
        _LISTDIR_CACHE[rel_dir] = scan_dir_md(rel_dir)
    return _LISTDIR_CACHE[rel_dir]


def folded_stems(rel_dir):
    """[(stem, abs_path, casefolded_stem)] for one dir; casefolding is done once."""
    if rel_dir not in _FOLDED_CACHE:
        abs_dir = VAULT_ROOT / rel_dir
        _FOLDED_CACHE[rel_dir] = [
            (fname[:-3], abs_dir / fname, fname[:-3].casefold())
            for fname in listdir_md(rel_dir)
        ]
    return _FOLDED_CACHE[rel_dir]


def union_type_dirs(type_keys):
    out = []
    for key in type_keys:
        for rel_dir in type_dirs(key):
            if rel_dir not in out:
                out.append(rel_dir)
    return out


def exact_filename_paths(name, type_key):
    found = []
    for rel_dir in type_dirs(type_key):
        candidate = VAULT_ROOT / rel_dir / f"{name}.md"
        if candidate.is_file():
            found.append(candidate)
        if rel_dir == "wiki/sources" and not name.startswith("@"):
            at = VAULT_ROOT / rel_dir / f"@{name}.md"
            if at.is_file():
                found.append(at)
    return found


def filename_stems_wanted(name, rel_dir):
    stems = {name.casefold()}
    if rel_dir == "wiki/sources" and not name.startswith("@"):
        stems.add(("@" + name).casefold())
    return stems


def stage_filename(store, name, type_key):
    for path in exact_filename_paths(name, type_key):
        add_candidate(store, path, "filename", 1.0, title=path.stem)
    q = name.casefold()
    for rel_dir in type_dirs(type_key):
        wanted = filename_stems_wanted(name, rel_dir)
        for stem, path, folded in folded_stems(rel_dir):
            if folded in wanted:
                add_candidate(store, path, "filename", 0.95, title=stem)
            elif q and q in folded:
                add_candidate(store, path, "substring", 0.6, title=stem)


def rg_alias_files(queries, rel_dirs):
    """One rg pass for the whole batch: files containing ANY of the queries."""
    rg = shutil.which("rg")
    dirs = [str(VAULT_ROOT / d) for d in rel_dirs if (VAULT_ROOT / d).is_dir()]
    if not dirs:
        return []
    if not rg:
        return None
    cmd = [rg, "-l", "--glob", "*.md", "-F"]
    for query in queries:
        cmd.extend(["-e", query])
    cmd.extend(["--", *dirs])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    hits = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        hits.append(Path(line))
    return hits


def fallback_alias_files(rel_dirs):
    paths = []
    for rel_dir in rel_dirs:
        abs_dir = VAULT_ROOT / rel_dir
        for fname in listdir_md(rel_dir):
            paths.append(abs_dir / fname)
    return paths


def build_alias_index(queries, type_keys):
    """Scan alias-bearing pages once for the whole batch.

    Returns [(abs_path, vault_rel, aliases, title, contains)], where `contains`
    is the set of queries whose literal text occurs in the page — the same gate
    rg applied per query before batching, so a batch answers exactly what N
    single-query runs answer. `contains` is None in the rg-less fallback, which
    never had that gate.
    """
    rel_dirs = union_type_dirs(type_keys)
    hits = rg_alias_files(queries, rel_dirs)
    narrow = hits is not None
    if not narrow:
        # rg missing: scan listed files in type dirs (filename inventory only).
        log("wiki-resolve: rg not found; scanning listed filenames for aliases")
        hits = fallback_alias_files(rel_dirs)
    index = []
    for raw in hits:
        path = Path(raw)
        if not path.is_absolute():
            path = VAULT_ROOT / path
        if not path.is_file():
            continue
        try:
            rel = vault_rel(path)
        except ValueError:
            continue
        if is_index_path(rel):
            continue
        text = read_prefix(path, -1 if narrow else FM_READ_BYTES)
        fm, _ = split_frontmatter(text)
        aliases = as_list(fm.get("aliases"))
        if not aliases:
            continue
        contains = frozenset(q for q in queries if q in text) if narrow else None
        index.append((path, rel, aliases, title_from_prefix(text, path.stem), contains))
    return index


def stage_alias(store, query, type_key, alias_index):
    allowed = tuple(d + "/" for d in type_dirs(type_key))
    for path, rel, aliases, title, contains in alias_index:
        if not rel.startswith(allowed):
            continue
        if contains is not None and query not in contains:
            continue
        if not alias_hits(query, aliases):
            continue
        add_candidate(store, path, "alias", 0.9, title=title, aliases=aliases)


def bm25_ready():
    retrieve = SCRIPT_DIR / "retrieve.py"
    index = VAULT_ROOT / ".vault-meta" / "bm25" / "index.json"
    return retrieve.is_file() and index.is_file()


def stage_bm25(store, query, type_key, top):
    if not bm25_ready():
        return
    retrieve = SCRIPT_DIR / "retrieve.py"
    cmd = [sys.executable, str(retrieve), query, "--top", str(top), "--no-rerank"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        log(f"wiki-resolve: retrieve.py failed: {e}")
        return
    if proc.returncode == 10:
        log("wiki-resolve: retrieve exit 10 (not provisioned); skip bm25")
        return
    if proc.returncode != 0:
        log(f"wiki-resolve: retrieve.py exit {proc.returncode}; skip bm25")
        return
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log("wiki-resolve: retrieve.py returned invalid JSON; skip bm25")
        return
    allowed = set(type_dirs(type_key))
    for cand in payload.get("candidates") or []:
        page_path = cand.get("page_path") or ""
        if not page_path:
            continue
        rel = page_path.replace("\\", "/")
        if is_index_path(rel):
            continue
        prefix = str(Path(rel).parent).replace("\\", "/")
        if type_key != "any" and prefix not in allowed:
            continue
        abs_p = VAULT_ROOT / rel
        if not abs_p.is_file():
            continue
        score = cand.get("bm25_score")
        if score is None:
            score = cand.get("rerank_score", 0.5)
        add_candidate(store, abs_p, "bm25", float(score), title=Path(rel).stem)


def enrich_kept(candidates):
    for rec in candidates:
        path = VAULT_ROOT / rec["path"]
        text = read_prefix(path, ONE_LINER_BYTES)
        rec["title"] = title_from_prefix(text, rec["title"])
        if not rec["aliases"]:
            rec["aliases"] = aliases_from_prefix(text)
        rec["one_liner"] = one_liner_from_prefix(text, rec["title"])


def sort_candidates(rows):
    type_order = {"entity": 0, "concept": 1, "source": 2}

    def key(rec):
        return (
            MATCH_RANK.get(rec["match"], 99),
            -float(rec.get("score", 0)),
            type_order.get(rec.get("type"), 9),
            rec["path"],
        )

    return sorted(rows, key=key)


def resolve_one(name, type_key, alias_index, top, min_score, use_bm25, enrich):
    store = {}
    stage_filename(store, name, type_key)
    stage_alias(store, name, type_key, alias_index)
    if use_bm25:
        stage_bm25(store, name, type_key, top)
    rows = [rec for rec in store.values() if float(rec.get("score", 0)) >= min_score]
    ranked = sort_candidates(rows)[:top]
    if enrich:
        enrich_kept(ranked)
    return ranked


def _is_comment_or_blank(raw):
    s = raw.lstrip("\ufeff").strip()
    return not s or s.startswith("#")


def collect_queries(args):
    """[(name, effective_type)] from positional args then --names-file, in order."""
    raw_queries = []
    for raw in args.names:
        cleaned = normalize_query_raw(raw)
        if _is_comment_or_blank(cleaned):
            continue
        raw_queries.append(cleaned)
    if args.names_file:
        try:
            text = Path(args.names_file).read_text(encoding="utf-8-sig")
        except OSError as e:
            err(f"ERR: cannot read --names-file: {e}")
            return None
        for line in text.splitlines():
            if _is_comment_or_blank(line):
                continue
            cleaned = normalize_query_raw(line)
            if _is_comment_or_blank(cleaned):
                continue
            raw_queries.append(cleaned)
    if not raw_queries:
        err("ERR: no query given (NAME... or --names-file)")
        return None
    queries = []
    for raw in raw_queries:
        name, type_key = split_query_type(raw, args.type)
        if not name.strip():
            err("ERR: NAME is empty" if len(raw_queries) == 1 else f"ERR: NAME is empty: {raw!r}")
            return None
        queries.append((name, type_key))
    return queries


def short_path(rel):
    return rel[len(WIKI_PREFIX):] if rel.startswith(WIKI_PREFIX) else rel


def one_line(text):
    """Keep the line-oriented shapes parseable when a query holds tabs/newlines."""
    return re.sub(r"\s+", " ", str(text)).strip()


def compact_line(result):
    cands = result["candidates"]
    head = f"{one_line(result['query'])}\t{result['type']}\t{'HIT' if cands else 'NONE'}\t"
    return head + "; ".join(
        f"{short_path(rec['path'])}({rec['match']} {float(rec.get('score', 0)):.2f})"
        for rec in cands
    )


def render_paths_only(results):
    out = []
    for result in results:
        out.append(f"# {one_line(result['query'])}")
        for rec in result["candidates"]:
            out.append(rec["path"])
    return "\n".join(out)


def main(argv=None):
    global QUIET
    parser = argparse.ArgumentParser(description="Resolve wiki page names without reading catalogs.")
    parser.add_argument("names", nargs="*",
                        help="Page names, aliases, or substrings; each may carry an "
                             "entity:/concept:/source:/any: prefix that overrides --type")
    parser.add_argument("--names-file", default=None,
                        help="File with one query per line (UTF-8, BOM ok; blank lines and "
                             "# comments ignored; entity:\"Name\" quotes are stripped)")
    parser.add_argument("--type", choices=("source", "entity", "concept", "any"), default="any")
    parser.add_argument("--top", type=int, default=None,
                        help="Max candidates per query (default 8; 3 with --compact)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="Drop candidates scoring below F (default 0)")
    parser.add_argument("--bm25", action="store_true", help="Merge retrieve.py BM25 hits")
    shape = parser.add_mutually_exclusive_group()
    shape.add_argument("--compact", action="store_true",
                       help="One TSV line per query, no aliases/one_liner (default --top 3)")
    shape.add_argument("--paths-only", action="store_true",
                       help="Candidate paths only, one per line, `# <query>` per query")
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    args = parser.parse_args(argv)

    QUIET = args.quiet
    reset_caches()

    if args.top is not None and args.top < 1:
        err("ERR: --top must be >= 1")
        return EXIT_USAGE
    top = args.top if args.top is not None else (COMPACT_TOP if args.compact else DEFAULT_TOP)

    queries = collect_queries(args)
    if queries is None:
        return EXIT_USAGE

    use_bm25 = args.bm25
    if use_bm25 and not bm25_ready():
        log("wiki-resolve: --bm25 skipped (retrieve.py or index missing)")
        use_bm25 = False

    enrich = not (args.compact or args.paths_only)
    alias_index = build_alias_index([name for name, _ in queries], [typ for _, typ in queries])

    results = []
    for name, type_key in queries:
        results.append({
            "query": name,
            "type": type_key,
            "candidates": resolve_one(
                name, type_key, alias_index, top, args.min_score, use_bm25, enrich
            ),
        })

    if len(results) == 1:
        log(f"wiki-resolve: {len(results[0]['candidates'])} candidate(s) for {results[0]['query']!r}")
    else:
        hit = sum(1 for r in results if r["candidates"])
        log(f"wiki-resolve: {len(results)} queries, {hit} hit, {len(results) - hit} none")

    if args.compact:
        print("\n".join(compact_line(r) for r in results))
    elif args.paths_only:
        print(render_paths_only(results))
    else:
        payload = results[0] if len(results) == 1 else {"results": results}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
