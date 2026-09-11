#!/usr/bin/env python3
"""wiki-graph.py — wiki ページのリンクグラフを構築し、retrieve の第 3 路(グラフ近傍)に使う。

BM25 と埋め込み再順位付けは語彙と意味の近さで候補を選ぶが、wiki が wikilink と共通出典で
明示した構造的な隣接は使っていなかった。本スクリプトは wiki ページを節、次の 2 種を辺とする
無向グラフを `.vault-meta/graph.json` に書く。

  link    本文または frontmatter `related` / `sources` の wikilink(重み 1.0。双方向なら 1.5)
  cocite  非 source ページ同士が frontmatter `sources` で同じ source を引く(共通出典ごとに
          1 / log2(2 + その source を引くページ数) を加算。皆が引く教科書章は薄く効く)

retrieve.py は BM25 上位ページを種として近傍を取り、候補に加える(`--no-graph` で無効)。
グラフが無い・空のときは retrieve の出力は従来と同一である。

使い方:
  python3 scripts/wiki-graph.py build                       # .vault-meta/graph.json を書く
  python3 scripts/wiki-graph.py neighbors "<page>" [--hops 1|2] [--top 10]
  python3 scripts/wiki-graph.py stats

<page> は `wiki/concepts/X.md` か `X`(ページ名)。環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる。

終了コード:
  0 — 成功
  2 — 使い方の誤り
  3 — グラフ未構築 / ページが節に無い
"""

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

GRAPH_REL = ".vault-meta/graph.json"
SCAN_DIRS = ("sources", "entities", "concepts", "questions", "surveys")
TYPE_BY_DIR = {"sources": "source", "entities": "entity", "concepts": "concept",
               "questions": "question", "surveys": "survey"}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
LINK_WEIGHT = 1.0
RECIPROCAL_BONUS = 0.5
HOP_DECAY = 0.5


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


# --------------------------------------------------------------------------- 読み取り

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


# --------------------------------------------------------------------------- 構築

def build():
    nodes = {}
    by_name = {}
    raw_links = {}
    cites = {}
    for sub, path in list_pages():
        rel = path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_text, body = split_frontmatter(text)
        name = path.stem
        nodes[rel] = {"name": name, "type": TYPE_BY_DIR[sub], "address": fm_scalar(fm_text, "address"),
                      "subtype": fm_scalar(fm_text, "entity_type") if sub == "entities" else None}
        by_name.setdefault(name, rel)
        # alias での解決は名前衝突を招くので行わない(wiki-resolve.py の領分)。ファイル名だけで結ぶ。
        targets = set()
        for t in WIKILINK_RE.findall(body):
            base = link_basename(t)
            if base:
                targets.add(base)
        # asks: / brief: は見ない。companion は SCAN_DIRS 外で、source と同じ
        # basename を持つため、stem 解決すると source 自己辺になる。
        for key in ("related", "sources"):
            for t in fm_list_links(fm_text, key):
                base = link_basename(t)
                if base:
                    targets.add(base)
        raw_links[rel] = targets
        if TYPE_BY_DIR[sub] != "source":
            srcs = {link_basename(t) for t in fm_list_links(fm_text, "sources")}
            cites[rel] = {s for s in srcs if s and s.startswith("@")}

    adj = {rel: {} for rel in nodes}

    def add(a, b, w):
        if a == b or a not in adj or b not in adj:
            return
        adj[a][b] = adj[a].get(b, 0.0) + w
        adj[b][a] = adj[b].get(a, 0.0) + w

    directed = set()
    for rel, targets in raw_links.items():
        for name in targets:
            other = by_name.get(name)
            if other and other != rel:
                directed.add((rel, other))
    link_edges = 0
    seen = set()
    for a, b in directed:
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        w = LINK_WEIGHT + (RECIPROCAL_BONUS if (b, a) in directed else 0.0)
        add(a, b, w)
        link_edges += 1

    citing = {}
    for rel, srcs in cites.items():
        for s in srcs:
            citing.setdefault(s, []).append(rel)
    cocite_pairs = 0
    for s, pages in citing.items():
        if len(pages) < 2 or len(pages) > 200:
            continue
        w = 1.0 / math.log2(2 + len(pages))
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                a, b = pages[i], pages[j]
                before = b in adj[a]
                add(a, b, w)
                if not before:
                    cocite_pairs += 1

    edges = sum(len(v) for v in adj.values()) // 2
    graph = {
        "version": 1,
        "built_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": nodes,
        "adj": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in adj.items()},
        "stats": {"nodes": len(nodes), "edges": edges, "link_edges": link_edges, "cocite_new_edges": cocite_pairs,
                  "isolated": sum(1 for v in adj.values() if not v)},
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


# --------------------------------------------------------------------------- 近傍展開

def expand(graph, seeds, hops=1, top=5, exclude=None):
    """seeds: [(page_path, weight)]。近傍ページを (path, score) の降順で返す。

    score = Σ_seed weight × edge_weight / log2(2 + deg(neighbor))。次数で割るのはハブ
    (皆が引く concept)が全ての問いで浮上するのを抑えるため。2 hop 目は HOP_DECAY を掛ける。
    """
    if graph_is_empty(graph):
        return []
    adj = graph["adj"]
    exclude = set(exclude or ()) | {p for p, _ in seeds}
    scores = {}
    frontier = [(p, w) for p, w in seeds if p in adj]
    for hop in range(max(1, hops)):
        decay = HOP_DECAY ** hop
        nxt = {}
        for p, w in frontier:
            for n, ew in adj.get(p, {}).items():
                deg = len(adj.get(n, {}))
                s = w * ew * decay / math.log2(2 + deg)
                if n not in exclude:
                    scores[n] = scores.get(n, 0.0) + s
                nxt[n] = max(nxt.get(n, 0.0), w * ew * decay)
        frontier = [(p, w) for p, w in nxt.items() if p not in exclude]
        exclude |= set(nxt)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top]


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
    sub.add_parser("stats")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        graph = build()
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

    if args.cmd == "neighbors":
        if args.top < 1:
            log("ERR: --top は 1 以上")
            return EXIT_USAGE
        rel = normalize_page(args.page, graph)
        if rel is None:
            log(f"ERR: not a graph node: {args.page}")
            return EXIT_MISSING
        rows = expand(graph, [(rel, 1.0)], hops=args.hops, top=args.top)
        out = [{"page": p, "name": graph["nodes"][p]["name"], "type": graph["nodes"][p]["type"],
                "score": round(s, 4), "direct_weight": graph["adj"][rel].get(p)} for p, s in rows]
        print(json.dumps({"page": rel, "hops": args.hops, "neighbors": out}, ensure_ascii=False, indent=2))
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
