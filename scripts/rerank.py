#!/usr/bin/env python3
"""rerank.py — query-time reranker for chunk candidates.

Takes a query string + a list of candidate chunks (from BM25, vector, or any
upstream stage) and reorders them using semantic similarity.

v1.7 strategy (in preference order, automatically chosen at runtime):
  1. If ollama is reachable AND the embedding model is pulled
       (qwen3-embedding:0.6b by default; override with WIKI_EMBED_MODEL)
       → embed the query, embed each candidate's contextualized_text,
         rank by cosine. Caches per-chunk embeddings in
         .vault-meta/embed-cache.json keyed by model + body_hash.
         Query / document prefixes follow the model's own convention
         (see EMBED_PREFIXES).
         WIKI_READ_ONLY=1 reads the cache but never writes it (callers from
         other projects whose sandbox cannot write into the vault); a
         long-running caller still reuses new embeddings in memory.
  2. Otherwise
       → no-op rerank: return candidates in input order with a synthesized
         note. Caller (retrieve.py) still gets a useful result; downstream
         drill-into-page logic is unchanged.

Future v1.7.x upgrade paths:
  - Cross-encoder reranker (sentence-transformers BGE-base) if installed
  - Cohere Rerank API if COHERE_API_KEY set
  - Voyage Rerank API if VOYAGE_API_KEY set

Mirrors the localhost-only OLLAMA_URL guard from scripts/tiling-check.py:
remote ollama endpoints require --allow-remote-ollama because page bodies
are POSTed as embedding input.

Usage:
  rerank.py "query string" --candidates candidates.json [--top 5]
  rerank.py "query string" --candidates - --top 5    # stdin
  rerank.py --peek "query string"                     # show strategy chosen

Candidates JSON shape:
  [{"chunk_id": "c-000042:3", "path": ".vault-meta/chunks/.../chunk-003.json", "score": 7.1}, ...]

Output: ranked candidates with `rerank_score` added.

Exit codes:
  0 — success
  2 — usage error
  3 — candidate input malformed
  10 — ollama unreachable (no-op rerank performed, exit 0 with note)
  11 — model not pulled (no-op rerank performed, exit 0 with note)
"""

import argparse
import fcntl
import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
META_DIR = VAULT_ROOT / ".vault-meta"
EMBED_CACHE_PATH = META_DIR / "embed-cache.json"
CACHE_LOCK = META_DIR / ".embed-cache.lock"

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
# Multilingual: the vault is mostly Japanese, and nomic-embed-text v1.5 is English-centric.
# 0.6b over 4b: on 26 paraphrased Japanese queries 4b ranked the target first 15 times and
# 0.6b 11 times, at about a quarter of the embedding time; the dense candidate channel in
# retrieve.py (dense-index.py) recovers more than that gap.
DEFAULT_MODEL = os.environ.get("WIKI_EMBED_MODEL") or "qwen3-embedding:0.6b"
OLLAMA_TIMEOUT_SEC = 3
# The first call after idle loads the model into memory, and a batch is one request.
EMBED_TIMEOUT_SEC = 120
EMBED_BATCH = 32

# (query_prefix, document_prefix) keyed by model family (name before the tag).
# Qwen3-Embedding is trained with an instruction on the query side only;
# nomic-embed-text expects task prefixes on both sides.
EMBED_PREFIXES = {
    "qwen3-embedding": (
        "Instruct: Given a question, retrieve wiki passages that answer the question\nQuery: ",
        "",
    ),
    "nomic-embed-text": ("search_query: ", "search_document: "),
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CANDIDATES = 3
EXIT_NO_OLLAMA = 10
EXIT_NO_MODEL = 11


def log(msg):
    print(msg, file=sys.stderr)


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def ollama_url(allow_remote):
    url = os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    if not allow_remote:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            log(f"ERR: OLLAMA_URL={url} points off-localhost (host={host!r}).")
            log("  Either: (a) run ollama locally — `systemctl --user start ollama` or `ollama serve`")
            log("  Or:     (b) pass --allow-remote-ollama through retrieve.py, which forwards it here.")
            log("  Or:     (c) unset OLLAMA_URL to fall back to the local default (127.0.0.1:11434).")
            sys.exit(EXIT_USAGE)
    return url


def ollama_alive(url):
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read(MAX_RESPONSE_BYTES))
            models = [m.get("name", "") for m in data.get("models", [])]
            return True, models
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return False, []


def model_present(model, models):
    """Compare with the tag: pulling `x:4b` does not make `x` (= `x:latest`) callable."""
    want = model if ":" in model else f"{model}:latest"
    return want in models


def prefixes(model):
    return EMBED_PREFIXES.get(model.split(":")[0], ("", ""))


def embed_many(url, model, texts):
    """Embed texts in one /api/embed request (about 15x faster than one call per text)."""
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read(MAX_RESPONSE_BYTES * max(1, len(texts))))
    embs = data.get("embeddings") or []
    if len(embs) != len(texts):
        raise ValueError(f"expected {len(texts)} embeddings, got {len(embs)}")
    return embs


def embed_one(url, model, text):
    return embed_many(url, model, [text])[0]


def embed_query(query, allow_remote=False):
    """Query embedding for retrieve.py's dense channel, or None when ollama/model is missing."""
    url = ollama_url(allow_remote)
    alive, models = ollama_alive(url)
    if not alive or not model_present(DEFAULT_MODEL, models):
        return None
    q_prefix, _ = prefixes(DEFAULT_MODEL)
    try:
        return embed_one(url, DEFAULT_MODEL, q_prefix + query)
    except Exception as e:
        log(f"query embed failed: {e}")
        return None


_cache_memo = (None, None)  # (mtime_ns, dict)


def load_cache():
    """埋め込みキャッシュをプロセス内に保持して返す。

    ファイルが変わらない限り同じ dict を返すので、常駐プロセス(wiki-mcp)では
    WIKI_READ_ONLY で保存しない新しい埋め込みも次の問いで再利用される。
    """
    global _cache_memo
    try:
        mtime = EMBED_CACHE_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cache_memo[1] is not None and _cache_memo[0] == mtime:
        return _cache_memo[1]
    cache = {}
    if mtime is not None:
        try:
            cache = json.loads(EMBED_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}
    _cache_memo = (mtime, cache)
    return cache


def save_cache(cache):
    """Persist the embed cache atomically.

    v1.7.2 / closes audit M7: previously used blocking fcntl.LOCK_EX with no
    timeout, which could hang indefinitely on a non-flock-capable filesystem
    (some NFS mounts, network shares, FUSE backends without lock support).
    Now uses LOCK_NB with a 3-attempt retry loop, then falls back to writing
    without the lock (with a WARN) so the rerank pipeline never hangs the
    user's session. The temp + os.replace pattern provides write atomicity
    even without the lock; the lock only serializes concurrent writers.
    """
    META_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(CACHE_LOCK), os.O_CREAT | os.O_WRONLY, 0o644)
    locked = False
    try:
        for attempt in range(3):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                time.sleep(0.1)
        if not locked:
            msg = ("WARN: rerank embed-cache lock unavailable after 3 tries; "
                   "writing unlocked (atomic via temp+rename). Concurrent writers "
                   "may overwrite each other's last update.")
            log(msg)
            # v1.9.1 / closes audit Data M1: also route to .vault-meta/hook.log so
            # the user sees the event via wiki-lint (stderr alone is invisible to
            # most callers; this matches the hook's logging shape).
            try:
                META_DIR.mkdir(parents=True, exist_ok=True)
                hook_log = META_DIR / "hook.log"
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                with hook_log.open("a", encoding="utf-8") as fh:
                    fh.write(f"{ts} rerank embed-cache lock unavailable; wrote unlocked\n")
            except OSError:
                pass  # never block on a logging failure
        tmp = EMBED_CACHE_PATH.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, EMBED_CACHE_PATH)
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def load_chunk(chunk_rel_path):
    p = VAULT_ROOT / chunk_rel_path
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def rerank(query, candidates, top_k=5, allow_remote=False, q_emb=None):
    """Returns candidates list, possibly truncated to top_k, with rerank_score added.
    Falls back to input-order if ollama is unavailable (still adds rerank_source: 'noop').
    q_emb: query embedding already computed by the caller (retrieve.py's dense channel).
    """
    url = ollama_url(allow_remote)
    alive, models = ollama_alive(url)
    if not alive:
        log("ollama unreachable — no-op rerank")
        for c in candidates:
            c["rerank_score"] = float(c.get("score", 0.0))
            c["rerank_source"] = "noop-no-ollama"
        return candidates[:top_k]
    if not model_present(DEFAULT_MODEL, models):
        log(f"model {DEFAULT_MODEL} not pulled — no-op rerank")
        for c in candidates:
            c["rerank_score"] = float(c.get("score", 0.0))
            c["rerank_source"] = "noop-no-model"
        return candidates[:top_k]

    q_prefix, d_prefix = prefixes(DEFAULT_MODEL)
    cache = load_cache()
    cache_dirty = False
    try:
        if q_emb is None:
            q_emb = embed_one(url, DEFAULT_MODEL, q_prefix + query)
    except Exception as e:
        log(f"query embed failed: {e}")
        for c in candidates:
            c["rerank_score"] = float(c.get("score", 0.0))
            c["rerank_source"] = "noop-embed-error"
        return candidates[:top_k]

    keys = {}     # id(candidate) -> cache key
    pending = []  # (candidate, cache_key, text) whose embedding is not cached
    for c in candidates:
        chunk = load_chunk(c.get("path", ""))
        if not chunk:
            c["rerank_score"] = 0.0
            c["rerank_source"] = "missing-chunk"
            continue
        cache_key = f"{DEFAULT_MODEL}:{chunk.get('body_hash', '')}"
        keys[id(c)] = cache_key
        if cache.get(cache_key):
            continue
        text = chunk.get("contextualized_text") or chunk.get("raw_text", "")
        pending.append((c, cache_key, d_prefix + text))

    failed = set()
    for n in range(0, len(pending), EMBED_BATCH):
        batch = pending[n:n + EMBED_BATCH]
        try:
            embs = embed_many(url, DEFAULT_MODEL, [t for _, _, t in batch])
        except Exception as e:
            log(f"embed failed for {len(batch)} chunk(s): {e}")
            failed.update(id(c) for c, _, _ in batch)
            continue
        for (_, key, _), emb in zip(batch, embs):
            cache[key] = emb
            cache_dirty = True

    for c in candidates:
        if id(c) not in keys:
            continue
        if id(c) in failed:
            c["rerank_score"] = float(c.get("score", 0.0))
            c["rerank_source"] = "embed-error"
            continue
        c["rerank_score"] = cosine(q_emb, cache.get(keys[id(c)]))
        c["rerank_source"] = f"cosine:{DEFAULT_MODEL}"

    if cache_dirty and os.environ.get("WIKI_READ_ONLY") != "1":
        save_cache(cache)

    ranked = sorted(candidates, key=lambda x: x.get("rerank_score", 0.0), reverse=True)
    return ranked[:top_k]


def main():
    parser = argparse.ArgumentParser(description="Rerank chunk candidates by semantic similarity.")
    parser.add_argument("query", nargs="?", help="Query text")
    parser.add_argument("--candidates", help="Path to candidates JSON or `-` for stdin",
                        default=None)
    parser.add_argument("--top", type=int, default=5, help="Top-K to return")
    parser.add_argument("--peek", action="store_true",
                        help="Print rerank strategy chosen and exit")
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Accept non-localhost OLLAMA_URL (potential data exfil)")
    args = parser.parse_args()

    if args.peek:
        if not args.query:
            log("--peek needs a query string")
            sys.exit(EXIT_USAGE)
        url = ollama_url(args.allow_remote_ollama)
        alive, models = ollama_alive(url)
        strategy = "noop-no-ollama"
        if alive:
            strategy = f"cosine:{DEFAULT_MODEL}" if model_present(DEFAULT_MODEL, models) else "noop-no-model"
        print(json.dumps({
            "query": args.query,
            "strategy": strategy,
            "ollama_url": url,
            "ollama_alive": alive,
            "model": DEFAULT_MODEL,
            "model_present": model_present(DEFAULT_MODEL, models),
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2))
        return EXIT_OK

    if not args.query or args.candidates is None:
        log("usage: rerank.py <query> --candidates <path|-> [--top N]")
        return EXIT_USAGE

    if args.candidates == "-":
        cand_text = sys.stdin.read()
    else:
        cand_text = Path(args.candidates).read_text(encoding="utf-8")
    try:
        candidates = json.loads(cand_text)
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a JSON list")
    except (json.JSONDecodeError, ValueError) as e:
        log(f"ERR: bad candidates JSON: {e}")
        return EXIT_CANDIDATES

    result = rerank(args.query, candidates, top_k=args.top,
                    allow_remote=args.allow_remote_ollama)
    print(json.dumps(result, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
