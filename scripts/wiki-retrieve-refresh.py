#!/usr/bin/env python3
"""wiki-retrieve-refresh.py — rechunk pages, rebuild the BM25 index, rebuild the link graph and theme chunks.

Does not modify contextual-prefix.py, bm25-index.py or wiki-graph.py; only subprocesses them.

Usage:
  wiki-retrieve-refresh.py --pages PATH [PATH ...] [--no-llm] [--no-graph]
  wiki-retrieve-refresh.py --all [--no-llm] [--no-graph]

--no-llm is the default. Pass --allow-egress to let contextual-prefix use an LLM.
The graph rebuild (`wiki-graph.py build`, a few seconds) runs after BM25 unless --no-graph;
its failure is reported (graph_ok=false) but does not fail the refresh.
`wiki-clusters.py build` follows the graph rebuild (surveys は読まない)。失敗は
clusters_ok=false であり retrieve は落とさない。

Stdout JSON: {pages, chunks_written, chunks_unchanged, bm25_ok, graph_ok, clusters_ok}

Exit codes:
  0 — success
  1 — contextual-prefix or bm25-index failed
  2 — usage error or missing sibling script
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
SCRIPT_DIR = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_CHILD = 1
EXIT_USAGE = 2

DONE_RE = re.compile(r"pages=(\d+)\s+chunks_written=(\d+)\s+chunks_unchanged=(\d+)")
WROTE_RE = re.compile(r"wrote=(\d+)")
SKIP_RE = re.compile(r"skipped\(unchanged\)=(\d+)")


def log(msg):
    print(msg, file=sys.stderr)


def scrape_prefix_stderr(stderr):
    done = DONE_RE.search(stderr)
    if done:
        return int(done.group(1)), int(done.group(2)), int(done.group(3))
    wrote = [int(x) for x in WROTE_RE.findall(stderr)]
    skipped = [int(x) for x in SKIP_RE.findall(stderr)]
    if wrote or skipped:
        return None, sum(wrote), sum(skipped)
    return None, None, None


def run_logged(cmd):
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = str(VAULT_ROOT)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
    return proc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh contextual chunks and BM25.")
    parser.add_argument("--pages", nargs="+", help="Vault-relative page paths")
    parser.add_argument("--all", action="store_true",
                        help="Rechunk knowledge wiki pages (asks/briefs は除外)")
    parser.add_argument("--no-llm", action="store_true", default=True,
                        help="Synthetic prefixes (default). Kept for CLI compatibility.")
    parser.add_argument("--allow-egress", action="store_true",
                        help="Allow contextual-prefix LLM tiers (overrides --no-llm)")
    parser.add_argument("--no-graph", action="store_true",
                        help="Skip rebuilding .vault-meta/graph.json (retrieve graph channel)")
    args = parser.parse_args(argv)

    if not args.pages and not args.all:
        log("ERR: require --pages PATH ... or --all")
        return EXIT_USAGE

    prefix_py = SCRIPT_DIR / "contextual-prefix.py"
    bm25_py = SCRIPT_DIR / "bm25-index.py"
    if not prefix_py.is_file():
        log(f"ERR: missing {prefix_py}")
        return EXIT_USAGE
    if not bm25_py.is_file():
        log(f"ERR: missing {bm25_py}")
        return EXIT_USAGE

    prefix_flags = []
    if args.allow_egress:
        prefix_flags.append("--allow-egress")
    else:
        prefix_flags.append("--no-llm")

    page_count = None
    chunks_written = 0
    chunks_unchanged = 0
    have_counts = False
    failed = False

    if args.all:
        log("wiki-retrieve-refresh: --all walks knowledge wiki/*.md (asks/briefs は除外); prefer --pages on ingest")
        cmd = [sys.executable, str(prefix_py), "--all", *prefix_flags]
        log(f"wiki-retrieve-refresh: {' '.join(cmd)}")
        proc = run_logged(cmd)
        scraped_pages, wrote, skipped = scrape_prefix_stderr(proc.stderr)
        if wrote is not None:
            chunks_written = wrote
            chunks_unchanged = skipped if skipped is not None else 0
            have_counts = True
        page_count = scraped_pages
        if proc.returncode != 0:
            failed = True
            log(f"wiki-retrieve-refresh: contextual-prefix exit {proc.returncode}")
    else:
        page_count = len(args.pages)
        for page in args.pages:
            cmd = [sys.executable, str(prefix_py), page, *prefix_flags]
            log(f"wiki-retrieve-refresh: {' '.join(cmd)}")
            proc = run_logged(cmd)
            _, wrote, skipped = scrape_prefix_stderr(proc.stderr)
            if wrote is not None:
                chunks_written += wrote
                chunks_unchanged += skipped if skipped is not None else 0
                have_counts = True
            if proc.returncode != 0:
                failed = True
                log(f"wiki-retrieve-refresh: contextual-prefix exit {proc.returncode} for {page}")

    bm25 = run_logged([sys.executable, str(bm25_py), "build"])
    bm25_ok = bm25.returncode == 0
    if not bm25_ok:
        failed = True
        log(f"wiki-retrieve-refresh: bm25-index.py build exit {bm25.returncode}")

    graph_ok = None
    graph_py = SCRIPT_DIR / "wiki-graph.py"
    if not args.no_graph and graph_py.is_file():
        graph = run_logged([sys.executable, str(graph_py), "build"])
        graph_ok = graph.returncode == 0
        if not graph_ok:
            log(f"wiki-retrieve-refresh: wiki-graph.py build exit {graph.returncode} (retrieve keeps working without it)")

    clusters_ok = None
    clusters_py = SCRIPT_DIR / "wiki-clusters.py"
    if clusters_py.is_file():
        clusters = run_logged([sys.executable, str(clusters_py), "build"])
        clusters_ok = clusters.returncode == 0
        if not clusters_ok:
            log(f"wiki-retrieve-refresh: wiki-clusters.py build exit {clusters.returncode} (retrieve keeps working without it)")

    if should_wipe_related(graph_ok, clusters_ok):
        wipe_related_after_rebuild(VAULT_ROOT)

    payload = {
        "pages": page_count,
        "chunks_written": chunks_written if have_counts else None,
        "chunks_unchanged": chunks_unchanged if have_counts else None,
        "bm25_ok": bm25_ok,
        "graph_ok": graph_ok,
        "clusters_ok": clusters_ok,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return EXIT_CHILD if failed else EXIT_OK


def should_wipe_related(graph_ok, clusters_ok):
    return bool(graph_ok or clusters_ok)


def wipe_related_after_rebuild(root):
    cache_py = SCRIPT_DIR / "wiki_related_cache.py"
    if not cache_py.is_file():
        return
    spec = importlib.util.spec_from_file_location("wiki_related_cache", cache_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.wipe_related_cache(root)


if __name__ == "__main__":
    sys.exit(main())
