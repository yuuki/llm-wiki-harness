#!/usr/bin/env python3
"""retrieve.py — hybrid retrieval orchestrator for the Compound Vault.

Pipeline (v1.7):
  query  →  bm25-index.py query (top-K candidates by BM25 over contextualized chunks)
         →  graph channel     (wiki-graph.py: neighbours of the top BM25 pages by
                              wikilink / shared-source edges, fused by RRF; only
                              when .vault-meta/graph.json exists, --no-graph disables.
                              An absent or empty graph leaves the output unchanged.)
         →  dense channel    (dense-index.py: pages whose lead embedding is nearest
                              to the query, added to the pool so a paraphrase with
                              no shared words can still reach the reranker; only
                              when the index for the rerank model exists and ollama
                              answers, --no-dense disables. An absent index leaves
                              the output unchanged.)
         →  rerank.py        (cosine on qwen3-embedding:0.6b vectors via ollama,
                              or no-op if ollama unavailable)
         →  fuse             (only with the dense channel: final order is RRF over
                              the BM25, page-dense and rerank ranks, `fused_score`)
         →  multi-query      (only with --also: the same pipeline per reformulation,
                              pages fused by weighted RRF, the original weighted 2)
         →  drill            (return chunk pages with absolute paths so the
                              caller can Read them and synthesize)

The pipeline lives in `Searcher.search()`; the CLI builds one Searcher per
call; a long-lived caller may keep one resident. The BM25 index is read from the SQLite
mirror (`.vault-meta/bm25/index.sqlite`) when it is current, else from JSON.

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
  "strategy": "bm25+rerank:cosine:qwen3-embedding:0.6b+graph+dense" | "bm25+noop-rerank",
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
      "rerank_source": "cosine:qwen3-embedding:0.6b",
      "snippet": "... first 200 chars of the chunk ...",
      "graph_score": 0.31,              # only on candidates the graph channel touched
      "channels": ["graph"],            # only on candidates the graph channel added
                                        # (["dense"] for pages only the dense channel found)
      "dense_score": 0.62,              # only on candidates the dense channel added
      "fused_score": 0.047,             # only when the dense channel ran (final sort key)
      "via": [{"from", "to", "kind?", "sources", "loc?", "hops"}]  # added or boosted; omitted on v1 graphs
    },
    ...
  ]
}

Usage:
  retrieve.py "your query here"           # standard: BM25 top-50 + dense top-20, rerank to top-5
  retrieve.py "query" --top 10            # change result count
  retrieve.py "query" --no-rerank         # skip rerank, BM25-only (+graph, RRF order)
  retrieve.py "query" --no-graph          # skip the graph channel
  retrieve.py "query" --no-dense          # skip the dense channel
  retrieve.py "query" --also "言い換え" --also "English term"   # agentic follow-up in one call
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
# Weight of the original question against each --also reformulation. On 25 paraphrased
# queries with three blind rewrites: weight 1 raised hit@1 12 -> 14 but lowered hit@3
# 20 -> 19; weight 2 kept hit@1 at 14 and no metric fell below the single query.
MULTI_ORIG_WEIGHT = 2.0
MULTI_PER_QUERY = 30


def rank_of(values):
    """1-based rank of each value, highest first (ties keep input order)."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0] * len(values)
    for r, i in enumerate(order, 1):
        ranks[i] = r
    return ranks


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


class Searcher:
    """検索に要るモジュールと索引を持ち回る。CLI は 1 回、MCP サーバは常駐で使う。

    BM25 索引は SQLite 鏡像があればそれを開く(数十 MB)。無ければ JSON を読む(数 GB)。
    索引ファイルやグラフが作り直されたら、次の検索で読み直す。
    """

    def __init__(self):
        self.bm25 = import_sibling("bm25_index", "bm25-index.py")
        self.reranker = import_sibling("rerank", "rerank.py")
        self._dense_mod = None
        self._dense = None
        self._dense_stamp = None
        self._graph_mod = None
        self._prefix_mod = None
        self._index = None
        self._index_stamp = None
        self._graph = None
        self._graph_stamp = None

    @staticmethod
    def _stamp(*paths):
        out = []
        for p in paths:
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def index(self):
        stamp = self._stamp(BM25_INDEX, getattr(self.bm25, "SQLITE_PATH", BM25_INDEX))
        if self._index is None or stamp != self._index_stamp:
            loader = getattr(self.bm25, "load_fast_index", self.bm25.load_index)
            self._index = loader()
            self._index_stamp = stamp
        return self._index

    def graph(self):
        if not GRAPH_JSON.is_file():
            return None
        stamp = self._stamp(GRAPH_JSON)
        if self._graph_mod is None:
            self._graph_mod = import_sibling("wiki_graph", "wiki-graph.py")
            self._prefix_mod = import_sibling("contextual_prefix", "contextual-prefix.py")
        if self._graph is None or stamp != self._graph_stamp:
            self._graph = self._graph_mod.load_graph(GRAPH_JSON)
            self._graph_stamp = stamp
        return self._graph

    def dense(self):
        if self._dense_mod is None:
            self._dense_mod = import_sibling("dense_index", "dense-index.py")
        paths = self._dense_mod.index_paths(self.reranker.DEFAULT_MODEL)
        if not paths[0].is_file():
            return None
        stamp = self._stamp(*paths)
        if self._dense is None or stamp != self._dense_stamp:
            self._dense = self._dense_mod.load_index(self.reranker.DEFAULT_MODEL)
            self._dense_stamp = stamp
        return self._dense

    def search(self, query, top=5, bm25_top=50, no_rerank=False, explain=False,
               allow_remote_ollama=False, no_graph=False, graph_top=5, graph_seeds=5,
               graph_hops=1, graph_people=False, no_dense=False, dense_top=20):
        bm25 = self.bm25
        bm25_index = self.index()

        bm25_hits = bm25.query(query, top_k=bm25_top, index=bm25_index)
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
        graph = self.graph() if (not no_graph and candidates) else None
        if graph and not self._graph_mod.graph_is_empty(graph):
            graph_mod, prefix_mod = self._graph_mod, self._prefix_mod

            def address_of(page):
                node = graph["nodes"].get(page) or {}
                if node.get("address"):
                    return node["address"]
                try:
                    return prefix_mod.derive_synthetic_address(VAULT_ROOT / page)
                except (ValueError, AttributeError):
                    return None

            added, boosted, via_by_page = graph_channel(
                graph_mod, graph, candidates, graph_seeds, graph_hops, graph_top, address_of,
                skip_subtypes=frozenset() if graph_people else SKIP_SUBTYPES,
                bm25_mod=bm25, index=bm25_index, query_text=query,
            )
            for c in candidates:
                page = c.get("page_path")
                if page in boosted and via_by_page.get(page):
                    c["via"] = via_by_page[page]
            via_count = sum(len(c.get("via") or []) for c in added)
            via_count += sum(len(c.get("via") or []) for c in candidates if c.get("page_path") in boosted)
            graph_info = {
                "active": True,
                "seeds": min(graph_seeds, len({c["page_path"] for c in candidates})),
                "added": [c["page_path"] for c in added],
                "boosted": sorted(boosted, key=lambda p: -boosted[p]),
                "via_count": via_count,
            }
            if added or boosted:
                candidates = rrf_fuse(candidates, added, boosted)
                log(f"graph: +{len(added)} page(s), {len(boosted)} boosted")

        # Dense channel: needs the query embedding, so it only runs when rerank does.
        dense_info = {"active": False}
        q_emb = None
        dense = None if (no_rerank or no_dense) else self.dense()
        if dense:
            q_emb = self.reranker.embed_query(query, allow_remote=allow_remote_ollama)
        if dense and q_emb:
            have = {c["page_path"] for c in candidates}
            added = []
            for score, entry in self._dense_mod.query(dense, q_emb, top_k=dense_top):
                if entry["path"] in have:
                    continue
                chunk_rel = f".vault-meta/chunks/{entry['address']}/chunk-000.json"
                try:
                    chunk = json.loads((VAULT_ROOT / chunk_rel).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                added.append({
                    "chunk_id": f"{entry['address']}:0",
                    "page_address": entry["address"],
                    "page_path": entry["path"],
                    "absolute_path": str((VAULT_ROOT / entry["path"]).resolve()),
                    "chunk_index": 0,
                    "bm25_score": 0.0,
                    "path": chunk_rel,
                    "snippet": chunk_snippet(chunk),
                    "channels": ["dense"],
                    "dense_score": round(score, 4),
                })
            candidates = candidates + added
            dense_info = {"active": True, "added": [c["page_path"] for c in added]}
            if added:
                log(f"dense: +{len(added)} page(s)")

        if no_rerank:
            final = candidates[:top]
            strategy = "bm25-only"
            for c in final:
                c["rerank_score"] = c["bm25_score"]
                c["rerank_source"] = "skipped"
        else:
            fuse = bool(dense_info["active"])
            final = self.reranker.rerank(
                query, candidates, top_k=len(candidates) if fuse else top,
                allow_remote=allow_remote_ollama, q_emb=q_emb,
            )
            if fuse and final and str(final[0].get("rerank_source", "")).startswith("cosine"):
                # The wider pool (BM25 top-50 + dense) lets more near-misses past a small
                # reranker; ranking on rerank cosine alone dropped hit@1 from 11 to 9 of 26.
                # RRF over the three ranks kept hit@1 at 11 and raised MRR 0.558 -> 0.613.
                pages = [c["page_path"] for c in final]
                dense_r = rank_of(self._dense_mod.page_scores(dense, q_emb, pages))
                bm25_r = rank_of([c.get("bm25_score") or -1.0 for c in final])
                rerank_r = rank_of([c.get("rerank_score") or 0.0 for c in final])
                for c, b, d, r in zip(final, bm25_r, dense_r, rerank_r):
                    c["fused_score"] = round(1 / (RRF_K + b) + 1 / (RRF_K + d) + 1 / (RRF_K + r), 6)
                final.sort(key=lambda c: c["fused_score"], reverse=True)
            final = final[:top]
            # Derive strategy from first candidate's rerank_source
            first_src = (final[0].get("rerank_source") if final else "unknown")
            strategy = f"bm25+rerank:{first_src}"
        if graph_info["active"] and (graph_info["added"] or graph_info["boosted"]):
            strategy += "+graph"
        if dense_info["active"] and dense_info["added"]:
            strategy += "+dense"

        # Dedupe by page (we may have multiple chunks of the same page; collapse to best)
        key = "fused_score" if final and "fused_score" in final[0] else "rerank_score"
        by_page = {}
        for c in final:
            addr = c.get("page_address")
            if addr not in by_page or c.get(key, 0) > by_page[addr].get(key, 0):
                by_page[addr] = c
        deduped = list(by_page.values())
        deduped.sort(key=lambda c: c.get(key, 0), reverse=True)

        out = {
            "query": query,
            "strategy": strategy,
            "top_k": top,
            "candidates": deduped[:top],
        }
        if explain:
            out["explain"] = {
                "bm25_candidate_count": len(bm25_hits),
                "post_rerank_count": len(final),
                "deduped_count": len(deduped),
                "bm25_top_param": bm25_top,
                "graph": graph_info,
                "dense": dense_info,
            }
        return out


def search_multi(searcher, queries, top=5, explain=False, **kw):
    """Run the pipeline once per query and fuse pages by weighted RRF.

    queries[0] is the original question (weight MULTI_ORIG_WEIGHT), the rest are the
    caller's reformulations (weight 1). Each page keeps the candidate row from the query
    that ranked it highest; `matched_queries` lists the indices of queries that hit it.
    """
    runs = [searcher.search(q, top=MULTI_PER_QUERY, explain=explain, **kw) for q in queries]
    score, best, matched = {}, {}, {}
    for qi, run in enumerate(runs):
        w = MULTI_ORIG_WEIGHT if qi == 0 else 1.0
        for rank, c in enumerate(run["candidates"], 1):
            page = c.get("page_path")
            score[page] = score.get(page, 0.0) + w / (RRF_K + rank)
            matched.setdefault(page, []).append(qi)
            if page not in best or rank < best[page][0]:
                best[page] = (rank, c)
    fused = []
    for page in sorted(score, key=lambda p: -score[p])[:top]:
        row = dict(best[page][1])
        row["multi_score"] = round(score[page], 6)
        row["matched_queries"] = matched[page]
        fused.append(row)
    out = {
        "query": queries[0],
        "also": queries[1:],
        "strategy": f"{runs[0]['strategy']}+multi{len(queries)}",
        "top_k": top,
        "candidates": fused,
    }
    if explain:
        out["explain"] = {"per_query": [{"query": q, "strategy": r["strategy"],
                                         "candidates": len(r["candidates"]),
                                         "explain": r.get("explain")}
                                        for q, r in zip(queries, runs)]}
    return out


def provisioned():
    """索引とチャンクがあれば None、無ければ利用者向けの説明文を返す。"""
    if not BM25_INDEX.is_file():
        return (f"ERR: no BM25 index at {BM25_INDEX}. Run `bash bin/setup-retrieve.sh` "
                "to provision, or fall back to wiki-resolve.py + rg + wiki-excerpt.py. "
                "Do not Read wiki/index.md.")
    if not CHUNKS_DIR.is_dir() or not any(CHUNKS_DIR.iterdir()):
        return (f"ERR: no chunks at {CHUNKS_DIR}. Run "
                "`python3 scripts/contextual-prefix.py --all` first.")
    return None


def main():
    parser = argparse.ArgumentParser(description="Hybrid retrieval over the vault.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--top", type=int, default=5, help="Final result count (post-rerank)")
    # 50, not 20: on 26 paraphrased queries 20 missed the target page in 6, 50 in 4,
    # and 100 or 200 still in 4 (those need the dense channel).
    parser.add_argument("--bm25-top", type=int, default=50,
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
    parser.add_argument("--no-dense", action="store_true",
                        help="Skip the dense channel even if .vault-meta/dense/ has an index")
    parser.add_argument("--dense-top", type=int, default=20,
                        help="Max pages the dense channel may consider (default 20)")
    parser.add_argument("--also", action="append", default=[], metavar="QUERY",
                        help="Reformulation to search too (repeatable); pages are fused by "
                             "RRF with the original question weighted 2")
    args = parser.parse_args()

    problem = provisioned()
    if problem:
        log(problem)
        return EXIT_NOT_PROVISIONED

    kw = dict(
        bm25_top=args.bm25_top, no_rerank=args.no_rerank,
        allow_remote_ollama=args.allow_remote_ollama,
        no_graph=args.no_graph, graph_top=args.graph_top, graph_seeds=args.graph_seeds,
        graph_hops=args.graph_hops, graph_people=args.graph_people,
        no_dense=args.no_dense, dense_top=args.dense_top,
    )
    searcher = Searcher()
    also = [q for q in args.also if q.strip() and q != args.query]
    if also:
        out = search_multi(searcher, [args.query, *also], top=args.top, explain=args.explain, **kw)
    else:
        out = searcher.search(args.query, top=args.top, explain=args.explain, **kw)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
