#!/usr/bin/env python3
"""bm25-index.py — sparse BM25 inverted index over contextualized wiki chunks.

Pure stdlib (no rank_bm25 dep). Standard Okapi BM25 with k1=1.5, b=0.75.
Indexes the `contextualized_text` field of every chunk under .vault-meta/chunks/,
emits a single JSON file at .vault-meta/bm25/index.json with the schema below.

Concurrency:
- Locks .vault-meta/.bm25.lock (fcntl exclusive) around any index write.
  A held lock is waited on (polling) for up to BM25_LOCK_WAIT_SEC seconds
  (default 180) before giving up. Failing fast made a concurrent `--all`
  rebuild in one session abort another session's `--pages` refresh, and
  callers under `set -e` then skipped their ledger updates.
- Atomic .tmp + rename for the index file.

Index schema (.vault-meta/bm25/index.json):
{
  "schema_version": 4,
  "tokenize": "cjk-bigram-v3",
  "params": {"k1": 1.5, "b": 0.75},
  "doc_count": 1234,
  "avg_dl": 933.1,
  "updated_at": "2026-05-17T...",
  "vocab": {
    "<term>": {"df": 17, "postings": [["c-000001:0", 3], ["c-000042:2", 1], ...]}
  },
  "docs": {
    "<chunk_id>": {"path": ".vault-meta/chunks/c-000001/chunk-000.json", "dl": 487}
  }
}

Chunk id format: "<page-address>:<chunk-index>" (e.g. "c-000042:3").

SQLite mirror (.vault-meta/bm25/index.sqlite):
  The JSON index is ~500 MB and parses to ~6 GB of Python objects. `build`
  also writes the same index to SQLite (postings packed as uint32 pairs), and
  `load_fast_index()` opens it read-only, keeping only the docs table in
  memory and fetching postings per query term. Scores are identical.
  `meta.updated_at` must equal the JSON's `updated_at`; otherwise the mirror is
  stale and callers fall back to the JSON. `bm25-index.py sqlite` converts an
  existing JSON index without re-tokenizing.

Tokenization: NFKC + casefold. Latin (and other non-CJK \\w) words keep
in-word apostrophes and hyphens. CJK runs become character bigrams; run
edges are also unigrams. Adjacent digit↔CJK pairs emit a boundary bigram
(e.g. 3台). ASCII digits are kept. ASCII stopwords filtered. Query and
index must use the same function (`wiki_cjk.tokenize`). Rebuild after
upgrade (schema_version 4, tokenize cjk-bigram-v3).

Query interface (used by retrieve.py at query time):
  bm25-index.py query "your text here" [--top 20]

Build interface:
  bm25-index.py build               # full rebuild (always; incremental is v1.7.x scope)
  bm25-index.py stats               # print index stats
  bm25-index.py sqlite              # (re)write the SQLite mirror from index.json

Exit codes:
  0 — success
  1 — lock acquisition timed out
  2 — usage error
  3 — index file missing or corrupt (query mode)
  4 — chunks directory missing
"""

import argparse
import fcntl
import json
import re
import sqlite3
from array import array
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_cjk import INDEX_SCHEMA_VERSION, TOKENIZE_ID, tokenize

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"
BM25_DIR = META_DIR / "bm25"
INDEX_PATH = BM25_DIR / "index.json"
SQLITE_PATH = BM25_DIR / "index.sqlite"
LOCK_PATH = META_DIR / ".bm25.lock"

K1 = 1.5
B = 0.75
SCHEMA_VERSION = INDEX_SCHEMA_VERSION

EXIT_OK = 0
EXIT_LOCK = 1
LOCK_WAIT_SEC = float(os.environ.get("BM25_LOCK_WAIT_SEC", "180"))
LOCK_POLL_SEC = 0.5
EXIT_USAGE = 2
EXIT_INDEX_MISSING = 3
EXIT_NO_CHUNKS = 4


def log(msg):
    print(msg, file=sys.stderr)


def acquire_lock():
    META_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o644)
    deadline = time.monotonic() + LOCK_WAIT_SEC
    announced = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                log(f"ERR: could not acquire bm25 lock within {LOCK_WAIT_SEC:g}s")
                sys.exit(EXIT_LOCK)
            if not announced:
                log("bm25 lock held by another build; waiting")
                announced = True
            time.sleep(LOCK_POLL_SEC)


def release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def discover_chunks():
    """Yield (chunk_id, path, contextualized_text) for every chunk on disk.

    The yielded `path` is relative to the directory two levels above CHUNKS_DIR
    (i.e. .vault-meta/chunks/<addr>/ → relative to the vault root). This works
    both in production (CHUNKS_DIR is `<vault>/.vault-meta/chunks`) and when
    tests monkey-patch CHUNKS_DIR to a sandbox `<tmp>/.vault-meta/chunks`.
    """
    if not CHUNKS_DIR.is_dir():
        log(f"ERR: no chunks directory at {CHUNKS_DIR}")
        sys.exit(EXIT_NO_CHUNKS)
    rel_root = CHUNKS_DIR.parent.parent
    for chunk_file in sorted(CHUNKS_DIR.glob("*/chunk-*.json")):
        try:
            data = json.loads(chunk_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"  skip (unreadable): {chunk_file} — {e}")
            continue
        address = data.get("page_address")
        idx = data.get("chunk_index")
        text = data.get("contextualized_text") or data.get("raw_text", "")
        if address is None or idx is None:
            continue
        chunk_id = f"{address}:{idx}"
        rel_path = str(chunk_file.relative_to(rel_root))
        yield chunk_id, rel_path, text


def build_index():
    docs = {}
    df = Counter()
    postings = defaultdict(list)

    for chunk_id, rel_path, text in discover_chunks():
        tokens = tokenize(text)
        tf = Counter(tokens)
        docs[chunk_id] = {"path": rel_path, "dl": len(tokens)}
        for term, count in tf.items():
            df[term] += 1
            postings[term].append([chunk_id, count])

    if not docs:
        log("WARN: no chunks indexed")
        return None

    avg_dl = sum(d["dl"] for d in docs.values()) / len(docs)
    vocab = {term: {"df": df[term], "postings": postings[term]}
             for term in sorted(df.keys())}

    return {
        "schema_version": SCHEMA_VERSION,
        "tokenize": TOKENIZE_ID,
        "params": {"k1": K1, "b": B},
        "doc_count": len(docs),
        "avg_dl": avg_dl,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vocab": vocab,
        "docs": docs,
    }


def write_index(index):
    BM25_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, INDEX_PATH)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def write_sqlite(index):
    """index(build_index / load_index の dict)を SQLite に写す。atomic に置き換える。"""
    BM25_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SQLITE_PATH.with_suffix(f".{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        con = sqlite3.connect(str(tmp))
        con.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE docs (id INTEGER PRIMARY KEY, cid TEXT, path TEXT, dl INTEGER);"
            "CREATE TABLE vocab (term TEXT PRIMARY KEY, df INTEGER, postings BLOB) WITHOUT ROWID;"
        )
        meta = {k: index.get(k) for k in
                ("schema_version", "tokenize", "params", "doc_count", "avg_dl", "updated_at")}
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [(k, json.dumps(v)) for k, v in meta.items()])
        ids = {}
        rows = []
        for i, (cid, d) in enumerate(index["docs"].items()):
            ids[cid] = i
            rows.append((i, cid, d["path"], d["dl"]))
        con.executemany("INSERT INTO docs VALUES (?, ?, ?, ?)", rows)

        def vrows():
            for term, v in index["vocab"].items():
                packed = array("I")
                for cid, cnt in v["postings"]:
                    packed.append(ids[cid])
                    packed.append(cnt)
                yield term, v["df"], packed.tobytes()

        con.executemany("INSERT INTO vocab VALUES (?, ?, ?)", vrows())
        con.commit()
        con.close()
        os.replace(tmp, SQLITE_PATH)
    finally:
        tmp.unlink(missing_ok=True)


class _SqliteVocab:
    def __init__(self, con, cids):
        self._con = con
        self._cids = cids

    def get(self, term, default=None):
        row = self._con.execute("SELECT df, postings FROM vocab WHERE term = ?", (term,)).fetchone()
        if not row:
            return default
        packed = array("I")
        packed.frombytes(row[1])
        cids = self._cids
        return {"df": row[0],
                "postings": [(cids[packed[i]], packed[i + 1]) for i in range(0, len(packed), 2)]}

    def __len__(self):
        return self._con.execute("SELECT count(*) FROM vocab").fetchone()[0]


def json_updated_at():
    """index.json 先頭の updated_at を全体を読まずに返す(build_index の鍵順に依存)。"""
    try:
        with INDEX_PATH.open("r", encoding="utf-8") as fh:
            head = fh.read(1024)
    except OSError:
        return None
    m = re.search(r'"updated_at":\s*"([^"]+)"', head)
    return m.group(1) if m else None


def load_sqlite_index():
    """SQLite 鏡像を開いて load_index() と同じ形の dict を返す。無い・古いなら None。

    vocab は語ごとに SQLite を引く遅延表、docs はメモリに載せる(数万件で数十 MB)。
    鏡像は置き換え(os.replace)でしか更新しないので immutable で開く。
    """
    if not SQLITE_PATH.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro&immutable=1", uri=True,
                              check_same_thread=False)
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        json_stamp = json_updated_at()
        if json_stamp is not None and json_stamp != meta.get("updated_at"):
            log("WARN: BM25 の SQLite 鏡像が index.json より古い。`bm25-index.py sqlite` で作り直す")
            con.close()
            return None
        cids = []
        docs = {}
        for _id, cid, path, dl in con.execute("SELECT id, cid, path, dl FROM docs ORDER BY id"):
            cids.append(cid)
            docs[cid] = {"path": path, "dl": dl}
    except sqlite3.Error as e:
        log(f"WARN: BM25 の SQLite 鏡像を開けない: {e}")
        return None
    idx = dict(meta)
    idx["docs"] = docs
    idx["vocab"] = _SqliteVocab(con, cids)
    idx["_sqlite"] = str(SQLITE_PATH)
    return idx


def load_fast_index():
    """SQLite 鏡像が新しければそれを、無ければ JSON を読む。"""
    return load_sqlite_index() or load_index()


def load_index():
    if not INDEX_PATH.is_file():
        log(f"ERR: no index at {INDEX_PATH}. Run `bm25-index.py build` first.")
        sys.exit(EXIT_INDEX_MISSING)
    try:
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERR: index corrupt: {e}")
        sys.exit(EXIT_INDEX_MISSING)
    if (idx.get("schema_version", 1) < SCHEMA_VERSION
            or idx.get("tokenize") != TOKENIZE_ID):
        log("WARN: BM25 索引の分かちが古い。`python3 scripts/bm25-index.py build` を実行せよ。")
    return idx


def _okapi_scores(idx, text, chunk_ids=None):
    """Okapi BM25 scores. chunk_ids が与えられたらその集合だけを足す。"""
    vocab = idx["vocab"]
    docs = idx["docs"]
    params = idx["params"]
    avg_dl = idx["avg_dl"] or 1.0
    N = idx["doc_count"]
    k1 = params["k1"]
    b = params["b"]
    qterms = tokenize(text)
    if not qterms:
        return {}
    allow = set(chunk_ids) if chunk_ids is not None else None
    scores = defaultdict(float)
    for term in qterms:
        v = vocab.get(term)
        if not v:
            continue
        df = v["df"]
        idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
        for cid, cnt in v["postings"]:
            if allow is not None and cid not in allow:
                continue
            dl = docs[cid]["dl"]
            denom = cnt + k1 * (1 - b + b * dl / avg_dl)
            scores[cid] += idf * (cnt * (k1 + 1)) / denom
    return scores


def query(text, top_k=20, index=None):
    idx = index if index is not None else load_index()
    docs = idx["docs"]
    scores = _okapi_scores(idx, text)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [
        {
            "chunk_id": cid,
            "score": round(score, 6),
            "path": docs[cid]["path"],
        }
        for cid, score in ranked
    ]


def best_chunk(query_text, page_address, index=None):
    """同一 page_address のチャンクから BM25 最大を返す。点が無ければ chunk-000。"""
    idx = index if index is not None else load_index()
    docs = idx["docs"]
    prefix = f"{page_address}:"
    cids = [cid for cid in docs if cid.startswith(prefix) and cid[len(prefix):].isdigit()]
    if not cids:
        return None
    scores = _okapi_scores(idx, query_text, chunk_ids=cids)
    if scores:
        cid, score = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
        return {"chunk_id": cid, "score": round(score, 6), "path": docs[cid]["path"]}
    zero = f"{page_address}:0"
    if zero in docs:
        return {"chunk_id": zero, "score": 0.0, "path": docs[zero]["path"]}
    return None


def stats():
    idx = load_index()
    print(json.dumps({
        "schema_version": idx.get("schema_version"),
        "tokenize": idx.get("tokenize"),
        "doc_count": idx["doc_count"],
        "avg_dl": round(idx["avg_dl"], 2),
        "vocab_size": len(idx["vocab"]),
        "updated_at": idx["updated_at"],
        "params": idx["params"],
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description="BM25 inverted index over wiki chunks.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="Build the index (full rebuild every time in v1.7).")

    sp_query = sub.add_parser("query", help="Query the index.")
    sp_query.add_argument("text", help="Query text")
    sp_query.add_argument("--top", type=int, default=20, help="Top-K results")

    sub.add_parser("stats", help="Print index stats.")
    sub.add_parser("sqlite", help="Write the SQLite mirror from the existing index.json.")

    args = parser.parse_args()

    if args.cmd == "build":
        fd = acquire_lock()
        try:
            index = build_index()
            if index is None:
                log("Nothing to index.")
                return EXIT_OK
            write_index(index)
            write_sqlite(index)
            log(f"Wrote {INDEX_PATH} (+ {SQLITE_PATH.name})  docs={index['doc_count']}  vocab={len(index['vocab'])}  avg_dl={index['avg_dl']:.1f}")
        finally:
            release_lock(fd)
        return EXIT_OK

    if args.cmd == "query":
        results = query(args.text, top_k=args.top, index=load_fast_index())
        print(json.dumps(results, indent=2))
        return EXIT_OK

    if args.cmd == "stats":
        stats()
        return EXIT_OK

    if args.cmd == "sqlite":
        fd = acquire_lock()
        try:
            write_sqlite(load_index())
            log(f"Wrote {SQLITE_PATH}")
        finally:
            release_lock(fd)
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
