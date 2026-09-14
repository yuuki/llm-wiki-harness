#!/usr/bin/env python3
"""wiki-related.py のディスクキャッシュ。スキルはここを Read しない。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

RELATED_REL = ".vault-meta/related"
LOCK_REL = ".vault-meta/.related.lock"
GEN_REL = ".vault-meta/related.generation"

ALLOCATED_RE = re.compile(r"^c-\d+$")
KEY_RE = re.compile(r"^(?:c-\d+|syn-[0-9a-f]{6})$")


def synthetic_address(seed):
    """contextual-prefix.derive_synthetic_address と同じ式。相対 path だけ使う。"""
    rel = Path(str(seed)).as_posix()
    return "syn-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:6]


def cache_key(seed, graph):
    addr = ((graph.get("nodes") or {}).get(seed) or {}).get("address")
    if isinstance(addr, str):
        addr = addr.strip()
        if ALLOCATED_RE.fullmatch(addr):
            return addr
    return synthetic_address(seed)


def cache_epoch(graph, clusters):
    return {
        "topology_sha256": (clusters or {}).get("topology_sha256") or "",
        "graph_built_at": (graph or {}).get("built_at") or "",
    }


def related_dir(root):
    return Path(root) / RELATED_REL


def cache_path(root, key):
    if not KEY_RE.fullmatch(key):
        raise ValueError("unsafe related cache key")
    return related_dir(root) / f"{key}.json"


def read_generation(root):
    path = Path(root) / GEN_REL
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_generation(root, gen):
    _atomic_write(Path(root) / GEN_REL, f"{gen}\n")


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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


@contextmanager
def related_lock(root):
    lock_path = Path(root) / LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def wipe_related_cache(root):
    """ディレクトリを消し、世代を上げる。ロック下で行う。"""
    with related_lock(root):
        wipe_related_cache_locked(root)


def wipe_related_cache_locked(root):
    _write_generation(root, read_generation(root) + 1)
    folder = related_dir(root)
    if folder.is_dir():
        shutil.rmtree(folder)


def confined_page(root, rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or Path(rel).is_absolute():
        return None
    if ".." in Path(rel).parts:
        return None
    base = Path(root).resolve()
    resolved = (base / rel).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    return resolved


def public_payload(envelope, root):
    roles = {}
    for role, cands in (envelope.get("roles") or {}).items():
        out = []
        for cand in cands or []:
            if not isinstance(cand, dict):
                continue
            page = cand.get("page_path")
            resolved = confined_page(root, page)
            if resolved is None:
                continue
            item = dict(cand)
            item["page_path"] = page
            item["absolute_path"] = str(resolved)
            out.append(item)
        roles[role] = out
    return {
        "seed": envelope.get("seed"),
        "roles": roles,
        "omitted": list(envelope.get("omitted") or []),
    }


def _envelope_ok(payload, seed, epoch, generation):
    if not isinstance(payload, dict):
        return False
    if payload.get("seed") != seed:
        return False
    if not isinstance(payload.get("roles"), dict):
        return False
    if not isinstance(payload.get("omitted"), list):
        return False
    if payload.get("topology_sha256") != epoch["topology_sha256"]:
        return "stale"
    if payload.get("graph_built_at") != epoch["graph_built_at"]:
        return "stale"
    if payload.get("generation") != generation:
        return "stale"
    return True


def read_related_cache(root, key, seed, epoch):
    """ヒットなら stdout 形。stale なら全削除して None。壊れていればそのファイルだけ無視。"""
    if not KEY_RE.fullmatch(key):
        return None
    with related_lock(root):
        path = cache_path(root, key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        status = _envelope_ok(payload, seed, epoch, read_generation(root))
        if status is True:
            return public_payload(payload, root)
        if status == "stale":
            wipe_related_cache_locked(root)
        return None


def write_related_cache(root, key, seed, epoch, payload, generation):
    """世代が変わっていれば書かない（削除が勝つ）。"""
    if not KEY_RE.fullmatch(key):
        return False
    if not isinstance(payload, dict) or payload.get("seed") != seed:
        return False
    envelope = {
        "topology_sha256": epoch["topology_sha256"],
        "graph_built_at": epoch["graph_built_at"],
        "seed": seed,
        "roles": payload.get("roles") or {},
        "omitted": payload.get("omitted") or [],
    }
    path = cache_path(root, key)
    with related_lock(root):
        if read_generation(root) != generation:
            return False
        envelope["generation"] = generation
        if path.is_file():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                old = None
            if isinstance(old, dict) and old.get("seed") not in (None, seed):
                return False
        _atomic_write(path, json.dumps(envelope, ensure_ascii=False))
        return True
