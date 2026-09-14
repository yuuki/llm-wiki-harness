#!/usr/bin/env python3
"""wiki-graph.py — wiki ページのリンクグラフを構築し、retrieve の第 3 路(グラフ近傍)に使う。

BM25 と埋め込み再順位付けは語彙と意味の近さで候補を選ぶ。本スクリプトは wiki ページを節、
次の種類を辺とする無向グラフを `.vault-meta/graph.json` に書く(version 2)。

  claim-link  主題節の命題が非 @ ページを出典付きで指す
  claim-cite  同じ命題が @ source を指す
  link        本文 wikilink(上に該当しない)
  fm-source   frontmatter sources の @ リンク
  fm-related  frontmatter related にだけある対
  cocite      非 source ページ同士が同じ @ source を引く

retrieve.py は BM25 上位ページを種として近傍を取り、候補に加える(`--no-graph` で無効)。
グラフが無い・空のときは retrieve の出力は従来と同一である。

使い方:
  python3 scripts/wiki-graph.py build
  python3 scripts/wiki-graph.py neighbors "<page>" [--hops 1|2] [--top 10]
  python3 scripts/wiki-graph.py why "<a>" "<b>"
  python3 scripts/wiki-graph.py stats

環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる。

終了コード:
  0 — 成功
  2 — 使い方の誤り
  3 — グラフ未構築 / ページが節に無い / 対が無い
"""

import argparse
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_claims import extract_claims

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

GRAPH_REL = ".vault-meta/graph.json"
SCAN_DIRS = ("sources", "entities", "concepts", "questions", "surveys")
CLAIM_DIRS = frozenset({"concepts", "questions", "surveys"})
TYPE_BY_DIR = {"sources": "source", "entities": "entity", "concepts": "concept",
               "questions": "question", "surveys": "survey"}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
KIND_RANK = {"claim-link": 5, "claim-cite": 4, "link": 3, "fm-source": 2, "fm-related": 1}
KIND_WEIGHT = {"claim-link": 1.5, "claim-cite": 1.0, "link": 1.0, "fm-source": 0.6, "fm-related": 0.4}
RECIPROCAL_BONUS = 0.5
HOP_DECAY = 0.5
EDGE_META_WARN_BYTES = 8 * 1024 * 1024
SOURCES_CAP = 8


def log(msg):
    print(msg, file=sys.stderr)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(path)
    except Exception:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


def pair_key(a, b):
    x, y = (a, b) if a < b else (b, a)
    return f"{x}\t{y}"


def split_frontmatter(text):
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return "", text


def fm_scalar(fm_text, key):
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", fm_text, flags=re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v or None


def fm_list_links(fm_text, key):
    """`key:` 配下の `- ...` 行から wikilink 名を集める(1 行形式 `key: [[A]]` も許す)。"""
    out = []
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", fm_text, flags=re.M)
    if not m:
        return out
    inline = m.group(1).strip()
    if inline and inline not in ("|", ">", "[]"):
        out.extend(WIKILINK_RE.findall(inline))
    rest = fm_text[m.end():]
    for line in rest.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            break
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item:
            out.extend(WIKILINK_RE.findall(item.group(1)))
    return [t.strip() for t in out]


def link_basename(target):
    t = target.strip()
    if not t or t.startswith((".raw/", "_attachments/", "papers/", "research/", "structures/", "notes/", "books/")):
        return None
    if t.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp", ".json")):
        return None
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[:-3]
    return t or None


def list_pages():
    out = []
    for sub in SCAN_DIRS:
        folder = VAULT_ROOT / "wiki" / sub
        if not folder.is_dir():
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".md") and not name.startswith("_"):
                out.append((sub, folder / name))
    return out


def resolve_source_stem(stem, nodes):
    if not stem or not stem.startswith("@"):
        return None
    rel = f"wiki/sources/{stem}.md"
    node = nodes.get(rel)
    if node and node["type"] == "source":
        return rel
    return None


def add_directed(store, src, dst, kind, sources=None, loc=None):
    if src == dst:
        return
    store[src, dst].append({"kind": kind, "sources": list(sources or []), "loc": loc})


# --------------------------------------------------------------------------- 構築

def build():
    nodes = {}
    by_name = {}
    raw = []
    for sub, path in list_pages():
        rel = path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_text, body = split_frontmatter(text)
        name = path.stem
        page_type = fm_scalar(fm_text, "type") or TYPE_BY_DIR[sub]
        nodes[rel] = {"name": name, "type": page_type, "address": fm_scalar(fm_text, "address"),
                      "subtype": fm_scalar(fm_text, "entity_type") if sub == "entities" else None}
        by_name.setdefault(name, rel)
        body_names = []
        for t in WIKILINK_RE.findall(body):
            base = link_basename(t)
            if base and base not in body_names:
                body_names.append(base)
        fm_related = []
        for t in fm_list_links(fm_text, "related"):
            base = link_basename(t)
            if base and base not in fm_related:
                fm_related.append(base)
        fm_sources = []
        for t in fm_list_links(fm_text, "sources"):
            base = link_basename(t)
            if base and base.startswith("@") and base not in fm_sources:
                fm_sources.append(base)
        claims = []
        if sub in CLAIM_DIRS:
            claims = extract_claims(rel, include_inbox=False, text=text)
        raw.append((rel, page_type, body_names, fm_related, fm_sources, claims))

    directed = defaultdict(list)
    citing = defaultdict(set)
    claim_citing = defaultdict(set)

    for rel, page_type, body_names, fm_related, fm_sources, claims in raw:
        if page_type != "source":
            for stem in fm_sources:
                if resolve_source_stem(stem, nodes):
                    citing[stem].add(rel)
            for claim in claims:
                if claim.get("kind") != "proposition":
                    continue
                resolved_sources = []
                for stem in claim["sources"]:
                    src = resolve_source_stem(stem, nodes)
                    if not src:
                        continue
                    citing[stem].add(rel)
                    claim_citing[stem].add(rel)
                    resolved_sources.append(src)
                    add_directed(directed, rel, src, "claim-cite", [src], claim.get("section"))
                loc = claim.get("section")
                for t in claim.get("targets") or []:
                    other = by_name.get(t)
                    if other and other != rel:
                        add_directed(directed, rel, other, "claim-link", resolved_sources, loc)
        for name in body_names:
            other = by_name.get(name)
            if other and other != rel:
                add_directed(directed, rel, other, "link")
        for name in fm_related:
            other = by_name.get(name)
            if other and other != rel:
                add_directed(directed, rel, other, "fm-related")
        for stem in fm_sources:
            src = resolve_source_stem(stem, nodes)
            if src and src != rel:
                add_directed(directed, rel, src, "fm-source", [src])

    pairs = defaultdict(lambda: {"recs": [], "dirs": set()})
    for (src, dst), recs in directed.items():
        key = pair_key(src, dst)
        pairs[key]["recs"].extend(recs)
        pairs[key]["dirs"].add((src, dst))

    adj = {rel: {} for rel in nodes}

    def add_weight(a, b, w):
        if a == b or a not in adj or b not in adj:
            return
        adj[a][b] = adj[a].get(b, 0.0) + w
        adj[b][a] = adj[b].get(a, 0.0) + w

    edge_meta = {}
    sources_truncated = 0
    claim_link_edges = 0
    claim_cite_edges = 0
    link_edges = 0

    for key, info in pairs.items():
        a, b = key.split("\t")
        kinds = [r["kind"] for r in info["recs"] if r["kind"] in KIND_RANK]
        if not kinds:
            continue
        winning = max(kinds, key=lambda k: KIND_RANK[k])
        recip = (a, b) in info["dirs"] and (b, a) in info["dirs"]
        w = KIND_WEIGHT[winning]
        if recip and winning in ("claim-link", "link"):
            w += RECIPROCAL_BONUS
        add_weight(a, b, w)
        if winning == "claim-link":
            claim_link_edges += 1
        elif winning == "claim-cite":
            claim_cite_edges += 1
        if winning in ("link", "claim-link"):
            link_edges += 1
        if winning in ("claim-link", "claim-cite"):
            srcs, loc = [], None
            for rec in info["recs"]:
                if rec["kind"] != winning:
                    continue
                for s in rec["sources"]:
                    if s not in srcs:
                        srcs.append(s)
                if loc is None and rec.get("loc"):
                    loc = rec["loc"]
            if len(srcs) > SOURCES_CAP:
                srcs = srcs[:SOURCES_CAP]
                sources_truncated += 1
            meta = {"kind": winning, "sources": srcs}
            if loc:
                meta["loc"] = loc
            edge_meta[key] = meta

    cocite_pairs = 0
    cocite_truncated = set()
    for stem, pages_set in citing.items():
        pages = sorted(pages_set)
        if len(pages) < 2 or len(pages) > 200:
            continue
        w = 1.0 / math.log2(2 + len(pages))
        src = resolve_source_stem(stem, nodes)
        from_claim = claim_citing.get(stem, set())
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                a, b = pages[i], pages[j]
                before = b in adj[a]
                add_weight(a, b, w)
                if not before:
                    cocite_pairs += 1
                if src and (a in from_claim or b in from_claim) and pair_key(a, b) not in pairs:
                    key = pair_key(a, b)
                    meta = edge_meta.get(key)
                    if meta is None:
                        edge_meta[key] = {"kind": "cocite", "sources": [src]}
                    elif meta.get("kind") == "cocite" and src not in meta["sources"]:
                        if len(meta["sources"]) >= SOURCES_CAP:
                            if key not in cocite_truncated:
                                sources_truncated += 1
                                cocite_truncated.add(key)
                        else:
                            meta["sources"].append(src)

    edges = sum(len(v) for v in adj.values()) // 2
    meta_json = json.dumps(edge_meta, ensure_ascii=False, separators=(",", ":"))
    edge_meta_bytes = len(meta_json.encode("utf-8"))
    if edge_meta_bytes > EDGE_META_WARN_BYTES:
        log(f"wiki-graph: warning edge_meta {edge_meta_bytes} bytes exceeds 8MB (not truncated)")

    graph = {
        "version": 2,
        "built_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": nodes,
        "adj": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in adj.items()},
        "edge_meta": edge_meta,
        "stats": {
            "nodes": len(nodes), "edges": edges, "link_edges": link_edges,
            "cocite_new_edges": cocite_pairs,
            "isolated": sum(1 for v in adj.values() if not v),
            "claim_link_edges": claim_link_edges, "claim_cite_edges": claim_cite_edges,
            "edge_meta": len(edge_meta), "sources_truncated": sources_truncated,
            "edge_meta_bytes": edge_meta_bytes,
        },
    }
    return graph


def load_graph(path=None):
    p = path or (VAULT_ROOT / GRAPH_REL)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def graph_is_empty(graph):
    return not graph or not graph.get("adj") or all(not v for v in graph["adj"].values())


def lookup_meta(graph, a, b):
    meta = (graph.get("edge_meta") or {}).get(pair_key(a, b))
    return meta


def hop_via(graph, frm, to, hop_i):
    d = {"from": frm, "to": to, "sources": [], "hops": hop_i}
    meta = lookup_meta(graph, frm, to)
    if meta:
        d["kind"] = meta["kind"]
        d["sources"] = list(meta.get("sources") or [])
        if meta.get("loc"):
            d["loc"] = meta["loc"]
    return d


def reconstruct_via(graph, pred, n, seed):
    """pred[seed][node] = predecessor. Walk seed ← … ← n."""
    chain = [n]
    cur = n
    seen = {n}
    while cur != seed:
        prev = pred.get(seed, {}).get(cur)
        if prev is None or prev in seen:
            return []
        chain.append(prev)
        seen.add(prev)
        cur = prev
    chain.reverse()
    if len(chain) < 2:
        return []
    return [hop_via(graph, chain[i], chain[i + 1], i + 1) for i in range(len(chain) - 1)]


def expand(graph, seeds, hops=1, top=5, exclude=None):
    """seeds: [(page_path, weight)]。近傍を (path, score, via) の降順で返す。

    score = Σ_seed weight × edge_weight / log2(2 + deg(neighbor))。2 hop 目は HOP_DECAY。
    via は最大寄与種の経路。同点は seeds 引数の並びの先勝ち。
    """
    if graph_is_empty(graph):
        return []
    adj = graph["adj"]
    seed_order = [p for p, _ in seeds]
    exclude = set(exclude or ()) | {p for p, _ in seeds}
    scores = {}
    contrib = defaultdict(lambda: defaultdict(float))
    pred = defaultdict(dict)
    pred_str = defaultdict(dict)
    origin = {p: p for p, _ in seeds if p in adj}
    frontier = [(p, w) for p, w in seeds if p in adj]
    for hop in range(max(1, hops)):
        decay = HOP_DECAY ** hop
        nxt = {}
        for p, w in frontier:
            seed = origin.get(p)
            if seed is None:
                continue
            for n, ew in adj.get(p, {}).items():
                deg = len(adj.get(n, {}))
                strength = w * ew * decay
                s = strength / math.log2(2 + deg)
                if n not in exclude:
                    scores[n] = scores.get(n, 0.0) + s
                    contrib[n][seed] += s
                    prev = pred[seed].get(n)
                    best = pred_str[seed].get(n)
                    if prev is None or strength > best or (strength == best and p < prev):
                        pred[seed][n] = p
                        pred_str[seed][n] = strength
                        origin[n] = seed
                nxt[n] = max(nxt.get(n, 0.0), strength)
        frontier = [(p, w) for p, w in nxt.items() if p not in exclude]
        exclude |= set(nxt)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    out = []
    for page, score in ranked:
        best_seed, best_c, best_i = None, -1.0, 10**9
        for i, seed in enumerate(seed_order):
            c = contrib[page].get(seed, 0.0)
            if c > best_c or (c == best_c and i < best_i):
                best_seed, best_c, best_i = seed, c, i
        via = reconstruct_via(graph, pred, page, best_seed) if best_seed else []
        out.append((page, score, via))
    return out


def normalize_page(raw, graph):
    s = raw.strip()
    if s in graph["nodes"]:
        return s
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].split("|")[0]
    if s.endswith(".md"):
        s = s[:-3]
    for rel, node in graph["nodes"].items():
        if node["name"] == s:
            return rel
    return None


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="wiki のリンク・共通出典グラフ。")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("neighbors")
    p.add_argument("page")
    p.add_argument("--hops", type=int, default=1, choices=(1, 2))
    p.add_argument("--top", type=int, default=10)
    w = sub.add_parser("why")
    w.add_argument("a")
    w.add_argument("b")
    sub.add_parser("stats")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        graph = build()
        from wiki_related_cache import wipe_related_cache
        wipe_related_cache(VAULT_ROOT)
        atomic_write(VAULT_ROOT / GRAPH_REL, json.dumps(graph, ensure_ascii=False, separators=(",", ":")))
        log(f"wiki-graph: {graph['stats']['nodes']} nodes, {graph['stats']['edges']} edges → {GRAPH_REL}")
        print(json.dumps({"ok": True, "graph": GRAPH_REL, **graph["stats"]}, ensure_ascii=False))
        return EXIT_OK

    graph = load_graph()
    if graph is None:
        log(f"ERR: no graph at {GRAPH_REL}; run `python3 scripts/wiki-graph.py build`")
        return EXIT_MISSING

    if args.cmd == "stats":
        print(json.dumps(graph["stats"], ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "why":
        a = normalize_page(args.a, graph)
        b = normalize_page(args.b, graph)
        if a is None or b is None:
            log(f"ERR: not a graph node: {args.a if a is None else args.b}")
            return EXIT_MISSING
        meta = lookup_meta(graph, a, b)
        if meta:
            print(json.dumps(meta, ensure_ascii=False, indent=2))
            return EXIT_OK
        weight = (graph.get("adj") or {}).get(a, {}).get(b)
        if weight is None:
            log(f"ERR: no edge: {a} — {b}")
            return EXIT_MISSING
        print(json.dumps({"weight": weight}, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "neighbors":
        if args.top < 1:
            log("ERR: --top は 1 以上")
            return EXIT_USAGE
        rel = normalize_page(args.page, graph)
        if rel is None:
            log(f"ERR: not a graph node: {args.page}")
            return EXIT_MISSING
        rows = expand(graph, [(rel, 1.0)], hops=args.hops, top=args.top)
        out = []
        for p, s, via in rows:
            row = {"page": p, "name": graph["nodes"][p]["name"], "type": graph["nodes"][p]["type"],
                   "score": round(s, 4), "direct_weight": graph["adj"][rel].get(p), "via": via}
            out.append(row)
        print(json.dumps({"page": rel, "hops": args.hops, "neighbors": out}, ensure_ascii=False, indent=2))
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
