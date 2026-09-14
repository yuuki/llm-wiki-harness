#!/usr/bin/env python3
"""retrieve.py — hybrid retrieval orchestrator for the Compound Vault.

Pipeline (v1.7):
  query  →  bm25-index.py query (top-K candidates by BM25 over contextualized chunks)
         →  graph channel     (wiki-graph.py: neighbours of the top BM25 pages by
                              wikilink / shared-source edges, fused by RRF; only
                              when .vault-meta/graph.json exists, --no-graph disables.
                              An absent or empty graph leaves the output unchanged.)
         →  rerank.py        (cosine on nomic-embed-text vectors via ollama,
                              or no-op if ollama unavailable)
         →  drill            (return chunk pages with absolute paths so the
                              caller can Read them and synthesize)

Loads sibling scripts as Python modules (no subprocess overhead). Falls back
gracefully when index or rerank stage is missing:
- If .vault-meta/bm25/index.json is absent     → exit 10 with friendly message;
                                                  caller falls back to wiki-resolve.py
                                                  + rg + wiki-excerpt.py (never index.md).
- If .vault-meta/chunks/ is empty              → exit 10 (same).
- If rerank stage cannot embed (no ollama)     → no-op rerank, returns BM25 order.

Output schema (JSON to stdout):
{
  "query": "...",
  "strategy": "bm25+rerank:cosine:nomic-embed-text" | "bm25+noop-rerank",
  "top_k": 5,
  "candidates": [
    {
      "chunk_id": "c-000042:3",
      "page_address": "c-000042",
      "page_path": "wiki/concepts/Foo.md",
      "absolute_path": "/abs/path/to/wiki/concepts/Foo.md",
      "chunk_index": 3,
      "bm25_score": 7.12,
      "rerank_score": 0.81,
      "rerank_source": "cosine:nomic-embed-text",
      "snippet": "... first 200 chars of the chunk ...",
      "graph_score": 0.31,              # only on candidates the graph channel touched
      "channels": ["graph"],            # only on candidates the graph channel added
      "via": [{"from", "to", "kind?", "sources", "loc?", "hops"}]  # added or boosted; omitted on v1 graphs
    },
    ...
  ]
}

Usage:
  retrieve.py "your query here"           # standard: BM25 top-20, rerank to top-5
  retrieve.py "query" --top 10            # change result count
  retrieve.py "query" --no-rerank         # skip rerank, BM25-only (+graph, RRF order)
  retrieve.py "query" --no-graph          # skip the graph channel
  retrieve.py "query" --graph-top 8 --graph-hops 2   # widen the graph channel
  retrieve.py "query" --graph-people      # also let authors / organizations in via the graph
  retrieve.py "query" --explain           # include per-stage diagnostics

Exit codes:
  0 — success
  2 — usage error
  10 — feature not provisioned (no chunks or no BM25 index); caller falls back
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or SCRIPT_DIR.parent).resolve()
SCRIPTS_DIR = SCRIPT_DIR
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"
BM25_INDEX = META_DIR / "bm25" / "index.json"
GRAPH_JSON = META_DIR / "graph.json"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_PROVISIONED = 10

RRF_K = 60


def log(msg):
    print(msg, file=sys.stderr)


def import_sibling(name, filename):
    """Import a hyphenated sibling .py file as a Python module.

    Wrapped in try/except (v1.7.2; closes audit M5) so a syntax error or
    missing dependency in a sibling helper produces a friendly diagnostic
    instead of a bare Python traceback at the user's first retrieve call.
    """
    target = SCRIPTS_DIR / filename
    if not target.is_file():
        log(f"ERR: sibling helper {filename} not found at {target}")
        log("  Run `bash bin/setup-retrieve.sh --check` to verify the install.")
        sys.exit(EXIT_NOT_PROVISIONED)
    try:
        spec = importlib.util.spec_from_file_location(name, target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, SyntaxError, AttributeError) as e:
        log(f"ERR: failed to import sibling helper {filename}: {type(e).__name__}: {e}")
        log("  This likely means the helper script is corrupted or has a syntax error.")
        log("  Run `python3 scripts/<helper>.py --help` directly to see the underlying error.")
        log("  If it persists: re-clone the repo or check `git status` for local damage.")
        sys.exit(EXIT_NOT_PROVISIONED)


def chunk_snippet(chunk_data, max_chars=200):
    text = chunk_data.get("raw_text", "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


SKIP_SUBTYPES = frozenset({"person", "organization"})


def graph_channel(graph_mod, graph, candidates, seeds_n, hops, top, address_of,
                  skip_subtypes=SKIP_SUBTYPES, bm25_mod=None, index=None, query_text=""):
    """BM25 上位ページを種に近傍ページを取り、(追加候補, 既存候補への加点, via) を返す。

    追加候補は同一ページ内で問いとの BM25 が最大のチャンクを代表にする。点が無ければ
    chunk-000。著者・所属は既定では追加候補にしない(--graph-people で含める)。
    """
    seed_pages = []
    for c in candidates:
        p = c.get("page_path")
        if p and p not in seed_pages:
            seed_pages.append(p)
        if len(seed_pages) >= seeds_n:
            break
    if not seed_pages:
        return [], {}, {}
    seeds = [(p, 1.0 / (i + 1)) for i, p in enumerate(seed_pages)]
    bm25_pages = {c.get("page_path") for c in candidates}
    neighbours = graph_mod.expand(graph, seeds, hops=hops, top=top + len(bm25_pages))
    added, boosted, via_by_page = [], {}, {}
    attach_via = isinstance(graph.get("edge_meta"), dict)
    for page, score, via in neighbours:
        via_by_page[page] = via if attach_via else []
        if page in bm25_pages:
            boosted[page] = score
            continue
        if len(added) >= top:
            continue
        if (graph["nodes"].get(page) or {}).get("subtype") in skip_subtypes:
            continue
        addr = address_of(page)
        if not addr:
            continue
        picked = None
        if bm25_mod is not None:
            picked = bm25_mod.best_chunk(query_text, addr, index=index)
        if picked:
            chunk_rel = Path(picked["path"])
            chunk_path = VAULT_ROOT / chunk_rel
            chunk_id = picked["chunk_id"]
            chunk_index = int(chunk_id.rsplit(":", 1)[-1]) if ":" in chunk_id else 0
        else:
            chunk_rel = Path(".vault-meta") / "chunks" / addr / "chunk-000.json"
            chunk_path = VAULT_ROOT / chunk_rel
            chunk_id = f"{addr}:0"
            chunk_index = 0
        if not chunk_path.is_file():
            continue
        try:
            chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rec = {
            "chunk_id": chunk.get("chunk_id", chunk_id),
            "page_address": chunk.get("page_address", addr),
            "page_path": page,
            "absolute_path": str((VAULT_ROOT / page).resolve()),
            "chunk_index": chunk.get("chunk_index", chunk_index),
            "bm25_score": 0.0,
            "graph_score": round(score, 4),
            "channels": ["graph"],
            "path": chunk_rel.as_posix(),
            "snippet": chunk_snippet(chunk),
        }
        if attach_via and via:
            rec["via"] = via
        added.append(rec)
    return added, boosted, via_by_page


def rrf_fuse(candidates, added, boosted):
    """BM25 順位とグラフ順位の Reciprocal Rank Fusion で候補を並べ直す。

    グラフが何も足さず何も加点しなければ呼ばれない(出力は従来と同一)。
    """
    graph_rank = {}
    ordered = sorted(
        [(p, s) for p, s in boosted.items()] + [(c["page_path"], c["graph_score"]) for c in added],
        key=lambda ps: -ps[1],
    )
    for rank, (page, _) in enumerate(ordered):
        graph_rank.setdefault(page, rank)
    fused = []
    for rank, c in enumerate(candidates):
        score = 1.0 / (RRF_K + rank)
        page = c.get("page_path")
        if page in graph_rank:
            score += 1.0 / (RRF_K + graph_rank[page])
            c["graph_score"] = round(boosted[page], 4)
        fused.append((score, rank, c))
    base = len(candidates)
    for i, c in enumerate(added):
        score = 1.0 / (RRF_K + graph_rank[c["page_path"]])
        fused.append((score, base + i, c))
    fused.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in fused]


def main():
    parser = argparse.ArgumentParser(description="Hybrid retrieval over the vault.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--top", type=int, default=5, help="Final result count (post-rerank)")
    parser.add_argument("--bm25-top", type=int, default=20,
                        help="Candidate count from BM25 (pre-rerank)")
    parser.add_argument("--no-rerank", action="store_true",
                        help="Skip the rerank stage; return BM25-only")
    parser.add_argument("--explain", action="store_true",
                        help="Include per-stage diagnostics in output")
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Forwarded to rerank.py")
    parser.add_argument("--no-graph", action="store_true",
                        help="Skip the graph channel even if .vault-meta/graph.json exists")
    parser.add_argument("--graph-top", type=int, default=5,
                        help="Max pages the graph channel may add (default 5)")
    parser.add_argument("--graph-seeds", type=int, default=5,
                        help="BM25 top pages used as graph seeds (default 5)")
    parser.add_argument("--graph-hops", type=int, default=1, choices=(1, 2),
                        help="Neighbourhood radius (default 1)")
    parser.add_argument("--graph-people", action="store_true",
                        help="Let the graph channel add person / organization entities too")
    args = parser.parse_args()

    if not BM25_INDEX.is_file():
        log(f"ERR: no BM25 index at {BM25_INDEX}. Run `bash bin/setup-retrieve.sh` "
            "to provision, or fall back to wiki-resolve.py + rg + wiki-excerpt.py. "
            "Do not Read wiki/index.md.")
        return EXIT_NOT_PROVISIONED
    if not CHUNKS_DIR.is_dir() or not any(CHUNKS_DIR.iterdir()):
        log(f"ERR: no chunks at {CHUNKS_DIR}. Run "
            "`python3 scripts/contextual-prefix.py --all` first.")
        return EXIT_NOT_PROVISIONED

    bm25 = import_sibling("bm25_index", "bm25-index.py")
    reranker = import_sibling("rerank", "rerank.py")
    bm25_index = bm25.load_index()

    bm25_hits = bm25.query(args.query, top_k=args.bm25_top, index=bm25_index)
    log(f"bm25: {len(bm25_hits)} hits")

    candidates = []
    for h in bm25_hits:
        chunk_path = VAULT_ROOT / h["path"]
        try:
            chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        candidates.append({
            "chunk_id": h["chunk_id"],
            "page_address": chunk.get("page_address"),
            "page_path": chunk.get("page_path"),
            "absolute_path": str((VAULT_ROOT / chunk.get("page_path", "")).resolve()),
            "chunk_index": chunk.get("chunk_index"),
            "bm25_score": h["score"],
            "path": h["path"],
            "snippet": chunk_snippet(chunk),
        })

    graph_info = {"active": False}
    if not args.no_graph and GRAPH_JSON.is_file() and candidates:
        graph_mod = import_sibling("wiki_graph", "wiki-graph.py")
        graph = graph_mod.load_graph(GRAPH_JSON)
        if graph and not graph_mod.graph_is_empty(graph):
            prefix_mod = import_sibling("contextual_prefix", "contextual-prefix.py")

            def address_of(page):
                node = graph["nodes"].get(page) or {}
                if node.get("address"):
                    return node["address"]
                try:
                    return prefix_mod.derive_synthetic_address(VAULT_ROOT / page)
                except (ValueError, AttributeError):
                    return None

            added, boosted, via_by_page = graph_channel(
                graph_mod, graph, candidates, args.graph_seeds, args.graph_hops, args.graph_top, address_of,
                skip_subtypes=frozenset() if args.graph_people else SKIP_SUBTYPES,
                bm25_mod=bm25, index=bm25_index, query_text=args.query,
            )
            for c in candidates:
                page = c.get("page_path")
                if page in boosted and via_by_page.get(page):
                    c["via"] = via_by_page[page]
            via_count = sum(len(c.get("via") or []) for c in added)
            via_count += sum(len(c.get("via") or []) for c in candidates if c.get("page_path") in boosted)
            graph_info = {
                "active": True,
                "seeds": min(args.graph_seeds, len({c["page_path"] for c in candidates})),
                "added": [c["page_path"] for c in added],
                "boosted": sorted(boosted, key=lambda p: -boosted[p]),
                "via_count": via_count,
            }
            if added or boosted:
                candidates = rrf_fuse(candidates, added, boosted)
                log(f"graph: +{len(added)} page(s), {len(boosted)} boosted")

    if args.no_rerank:
        final = candidates[:args.top]
        strategy = "bm25-only"
        for c in final:
            c["rerank_score"] = c["bm25_score"]
            c["rerank_source"] = "skipped"
    else:
        final = reranker.rerank(
            args.query, candidates, top_k=args.top,
            allow_remote=args.allow_remote_ollama,
        )
        # Derive strategy from first candidate's rerank_source
        first_src = (final[0].get("rerank_source") if final else "unknown")
        strategy = f"bm25+rerank:{first_src}"
    if graph_info["active"] and (graph_info["added"] or graph_info["boosted"]):
        strategy += "+graph"

    # Dedupe by page (we may have multiple chunks of the same page; collapse to best)
    by_page = {}
    for c in final:
        addr = c.get("page_address")
        if addr not in by_page or c.get("rerank_score", 0) > by_page[addr].get("rerank_score", 0):
            by_page[addr] = c
    deduped = list(by_page.values())
    deduped.sort(key=lambda c: c.get("rerank_score", 0), reverse=True)

    out = {
        "query": args.query,
        "strategy": strategy,
        "top_k": args.top,
        "candidates": deduped[:args.top],
    }
    if args.explain:
        out["explain"] = {
            "bm25_candidate_count": len(bm25_hits),
            "post_rerank_count": len(final),
            "deduped_count": len(deduped),
            "bm25_top_param": args.bm25_top,
            "graph": graph_info,
        }

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
