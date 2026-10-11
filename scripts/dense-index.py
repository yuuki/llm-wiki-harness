#!/usr/bin/env python3
"""dense-index.py — page-level embedding index for the retrieve dense channel.

BM25 cannot surface a page whose wording does not overlap the query (a
paraphrased question, an English-titled concept asked about in Japanese).
This index embeds the lead of every chunked page (chunk-000: title + opening,
up to LEAD_CHARS) so retrieve.py can add the nearest pages to the rerank pool.

One vector per page, not per chunk: a page-level build is several times
cheaper in time and storage than embedding every chunk, for a channel whose
only job is recall (the reranker still scores the chunk).

Files (under .vault-meta/dense/, git-ignored with the rest of .vault-meta/):
  <model>.json  — {"version", "model", "dim", "pages": [{address, path, hash}]}
  <model>.f32   — L2-normalized float32 vectors, row i = pages[i]

The model and the document prefix come from rerank.py (DEFAULT_MODEL,
EMBED_PREFIXES), so the index and the query side always agree. Rebuilding
re-embeds only pages whose page_body_hash changed. Localhost-only, like
rerank.py.

Usage:
  dense-index.py build [--allow-remote-ollama]
  dense-index.py status

Exit codes:
  0 — success
  2 — usage error
  10 — ollama unreachable
  11 — model not pulled
"""

import argparse
import array
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or SCRIPT_DIR.parent).resolve()
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"
DENSE_DIR = META_DIR / "dense"

INDEX_VERSION = 1
LEAD_CHARS = 1200
BATCH = 32
CHECKPOINT = BATCH * 20

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_OLLAMA = 10
EXIT_NO_MODEL = 11


def log(msg):
    print(msg, file=sys.stderr)


def load_rerank():
    spec = importlib.util.spec_from_file_location("rerank", SCRIPT_DIR / "rerank.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def index_paths(model):
    stem = model.replace("/", "_").replace(":", "_")
    return DENSE_DIR / f"{stem}.json", DENSE_DIR / f"{stem}.f32"


def lead_text(chunk):
    """Title + opening of the page, without the Navigation line every page carries."""
    lines = [ln for ln in chunk.get("raw_text", "").splitlines()
             if not ln.startswith("Navigation:")]
    return "\n".join(lines).strip()[:LEAD_CHARS]


def normalize(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def load_index(model):
    meta_path, vec_path = index_paths(model)
    if not meta_path.is_file() or not vec_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        vecs = array.array("f")
        vecs.frombytes(vec_path.read_bytes())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if meta.get("version") != INDEX_VERSION or meta.get("model") != model:
        return None
    if len(vecs) != len(meta["pages"]) * meta["dim"]:
        return None
    return meta, vecs


def query(index, q_emb, top_k=20):
    """Return [(score, page_entry)] for the top_k pages nearest to q_emb."""
    meta, vecs = index
    dim = meta["dim"]
    if len(q_emb) != dim:
        return []
    q = normalize(q_emb)
    scored = []
    for i, entry in enumerate(meta["pages"]):
        row = vecs[i * dim:(i + 1) * dim]
        scored.append((sum(a * b for a, b in zip(q, row)), entry))
    scored.sort(key=lambda s: s[0], reverse=True)
    return scored[:top_k]


def page_scores(index, q_emb, paths):
    """Cosine of q_emb to each page path's lead vector; -1.0 for pages not in the index."""
    meta, vecs = index
    dim = meta["dim"]
    row_of = {e["path"]: i for i, e in enumerate(meta["pages"])}
    q = normalize(q_emb)
    out = []
    for path in paths:
        i = row_of.get(path)
        if i is None or len(q) != dim:
            out.append(-1.0)
            continue
        out.append(sum(a * b for a, b in zip(q, vecs[i * dim:(i + 1) * dim])))
    return out


def page_leads():
    """Yield (address, page_path, page_body_hash, text) for every chunked page."""
    for chunk_path in sorted(CHUNKS_DIR.glob("*/chunk-000.json")):
        try:
            chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        text = lead_text(chunk)
        if text:
            yield (chunk.get("page_address"), chunk.get("page_path"),
                   chunk.get("page_body_hash", ""), text)


def save(model, pages, fresh, reuse):
    """Write every page that has a vector (fresh or reused); return the entries written."""
    entries, out = [], array.array("f")
    dim = None
    for i, (addr, path, h, _) in enumerate(pages):
        row = fresh.get(i) or reuse.get((addr, h))
        if row is None:
            continue
        dim = dim or len(row)
        if len(row) != dim:
            continue
        entries.append({"address": addr, "path": path, "hash": h})
        out.extend(row)

    DENSE_DIR.mkdir(parents=True, exist_ok=True)
    meta_path, vec_path = index_paths(model)
    # Append, not with_suffix: "qwen3-embedding_0.6b.json" and ".f32" would share one tmp name.
    tmp_vec = vec_path.with_name(f"{vec_path.name}.{os.getpid()}.tmp")
    tmp_meta = meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")
    tmp_vec.write_bytes(out.tobytes())
    tmp_meta.write_text(json.dumps({"version": INDEX_VERSION, "model": model, "dim": dim or 0,
                                    "pages": entries}, ensure_ascii=False), encoding="utf-8")
    # Vectors first: a reader that sees the new manifest with the old vectors fails the
    # length check and falls back to no dense channel instead of misaligned rows.
    os.replace(tmp_vec, vec_path)
    os.replace(tmp_meta, meta_path)
    return entries


def build(allow_remote=False):
    rr = load_rerank()
    model = rr.DEFAULT_MODEL
    url = rr.ollama_url(allow_remote)
    alive, models = rr.ollama_alive(url)
    if not alive:
        log(f"ERR: ollama unreachable at {url}")
        return EXIT_NO_OLLAMA
    if not rr.model_present(model, models):
        log(f"ERR: model {model} not pulled (`ollama pull {model}`)")
        return EXIT_NO_MODEL
    _, d_prefix = rr.prefixes(model)

    old = load_index(model)
    reuse = {}
    if old:
        meta, vecs = old
        dim = meta["dim"]
        for i, e in enumerate(meta["pages"]):
            reuse[(e["address"], e["hash"])] = vecs[i * dim:(i + 1) * dim]

    pages = list(page_leads())
    todo = [i for i, (addr, _, h, _) in enumerate(pages) if (addr, h) not in reuse]
    log(f"dense-index: pages={len(pages)} reuse={len(pages) - len(todo)} embed={len(todo)} model={model}")

    fresh = {}
    started = time.time()
    for n in range(0, len(todo), BATCH):
        batch = todo[n:n + BATCH]
        embs = rr.embed_many(url, model, [d_prefix + pages[i][3] for i in batch])
        for i, emb in zip(batch, embs):
            fresh[i] = array.array("f", normalize(emb))
        done = n + len(batch)
        if done % CHECKPOINT == 0 or done == len(todo):
            rate = done / max(time.time() - started, 1e-6)
            log(f"dense-index: {done}/{len(todo)} ({rate:.1f} pages/s)")
            # Checkpoint: an interrupted first build resumes from here instead of from zero.
            entries = save(model, pages, fresh, reuse)

    if not todo:
        entries = save(model, pages, fresh, reuse)
    print(json.dumps({"model": model, "pages": len(entries), "embedded": len(fresh),
                      "reused": len(entries) - len(fresh)}))
    return EXIT_OK


def status():
    rr = load_rerank()
    model = rr.DEFAULT_MODEL
    idx = load_index(model)
    print(json.dumps({"model": model, "present": idx is not None,
                      "pages": len(idx[0]["pages"]) if idx else 0,
                      "dim": idx[0]["dim"] if idx else None}))
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(description="Page-level embedding index for retrieve.py.")
    parser.add_argument("command", choices=("build", "status"))
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Accept non-localhost OLLAMA_URL (page bodies are POSTed)")
    args = parser.parse_args()
    if args.command == "build":
        return build(args.allow_remote_ollama)
    return status()


if __name__ == "__main__":
    sys.exit(main())
