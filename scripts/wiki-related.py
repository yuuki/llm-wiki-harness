#!/usr/bin/env python3
"""wiki-related.py — ページを種にした配役つき近傍。

問い検索は retrieve.py のまま。本コマンドは vault 相対パス 1 つだけを受け取る。
`--query` は置かない。expand() は切断に使わない。点数式と reconstruct_via だけ借りる。
キャッシュは `.vault-meta/related/<key>.json`。スキルは stdout だけ見る。

使い方:
  python3 scripts/wiki-related.py "wiki/concepts/PagedAttention.md"

終了コード:
  0 — グラフ節。JSON を stdout に出す
  2 — 使い方の誤り
  3 — 種がグラフ節ではない
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

ROLE_CAP = 2
FILL_ORDER = ("contradiction", "evidence", "definition", "cluster")
NEEDLE_MIN = 6
HOP_DECAY = 0.5
SCAN_DIRS = ("sources", "entities", "concepts", "questions", "surveys")


def log(msg):
    print(msg, file=sys.stderr)


def vault_root():
    return Path(os.environ.get("WIKI_VAULT_ROOT") or SCRIPT_DIR.parent).resolve()


def import_sibling(name, filename):
    target = SCRIPT_DIR / filename
    if not target.is_file():
        log(f"ERR: sibling helper {filename} not found at {target}")
        sys.exit(EXIT_USAGE)
    spec = importlib.util.spec_from_file_location(name, target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


graph_mod = import_sibling("wiki_graph", "wiki-graph.py")
clusters_mod = import_sibling("wiki_clusters", "wiki-clusters.py")
contra_mod = import_sibling("contradiction_index", "contradiction-index.py")
resolve_mod = import_sibling("wiki_resolve", "wiki-resolve.py")
cache_mod = import_sibling("wiki_related_cache", "wiki_related_cache.py")


def missing_payload(seed):
    return {
        "seed": seed,
        "roles": {},
        "omitted": [{"role": "*", "reason": "not-a-graph-node"}],
    }


def by_name_map(graph):
    """graph 構築と同じ SCAN_DIRS 先勝ち。"""
    by_name = {}
    nodes = graph.get("nodes") or {}
    for sub in SCAN_DIRS:
        prefix = f"wiki/{sub}/"
        for rel in sorted(p for p in nodes if p.startswith(prefix)):
            name = (nodes[rel] or {}).get("name")
            if name:
                by_name.setdefault(name, rel)
    return by_name


def resolve_name(raw, graph, by_name):
    base = graph_mod.link_basename(raw)
    if not base:
        return None
    if base.startswith("@"):
        return graph_mod.resolve_source_stem(base, graph["nodes"])
    if base in by_name:
        return by_name[base]
    return graph_mod.normalize_page(base, graph)


def read_text(rel, root):
    path = root / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def page_title(rel, graph, root, cache):
    if rel in cache:
        return cache[rel]
    text = read_text(rel, root)
    fm, _ = graph_mod.split_frontmatter(text)
    title = graph_mod.fm_scalar(fm, "title")
    if not title:
        stem = Path(rel).stem
        title = resolve_mod.title_from_prefix(text, stem) if text else stem
    cache[rel] = title
    return title


def page_status(rel, root, cache):
    if rel in cache:
        return cache[rel]
    text = read_text(rel, root)
    fm, _ = graph_mod.split_frontmatter(text)
    cache[rel] = graph_mod.fm_scalar(fm, "status")
    return cache[rel]


def fm_resolved(rel, key, graph, by_name, root, cache):
    ck = (rel, key)
    if ck in cache:
        return cache[ck]
    text = read_text(rel, root)
    fm, _ = graph_mod.split_frontmatter(text)
    out = []
    for raw in graph_mod.fm_list_links(fm, key):
        got = resolve_name(raw, graph, by_name)
        if got and got not in out:
            out.append(got)
    cache[ck] = out
    return out


def deg(graph, page):
    return len((graph.get("adj") or {}).get(page) or {})


def bfs_neighborhood(graph, seed):
    """距離 1 と 2 を切らずに取る。expand() は呼ばない。

    戻り値: {page: {dist, score, pred, strength}}
    """
    adj = graph.get("adj") or {}
    d1 = {}
    for n, w in (adj.get(seed) or {}).items():
        score = w / math.log2(2 + deg(graph, n))
        d1[n] = {"dist": 1, "score": score, "pred": seed, "strength": float(w)}
    d2 = {}
    for mid, w1 in (adj.get(seed) or {}).items():
        for dst, w2 in (adj.get(mid) or {}).items():
            if dst == seed or dst in d1:
                continue
            strength = w1 * w2 * HOP_DECAY
            score = strength / math.log2(2 + deg(graph, dst))
            prev = d2.get(dst)
            if prev is None or strength > prev["strength"] or (
                strength == prev["strength"] and mid < prev["pred"]
            ):
                d2[dst] = {"dist": 2, "score": score, "pred": mid, "strength": strength}
    out = dict(d1)
    out.update(d2)
    return out


def pred_table(seed, neigh):
    pred = {seed: {}}
    for page, info in neigh.items():
        pred[seed][page] = info["pred"]
        mid = info["pred"]
        if mid != seed and mid not in pred[seed]:
            pred[seed][mid] = seed
    return pred


def via_for(graph, seed, dst, neigh):
    info = neigh.get(dst)
    if info is None or info["dist"] == 1:
        return [graph_mod.hop_via(graph, seed, dst, 1)]
    pred = pred_table(seed, neigh)
    via = graph_mod.reconstruct_via(graph, pred, dst, seed)
    if via:
        return via
    mid = info["pred"]
    return [
        graph_mod.hop_via(graph, seed, mid, 1),
        graph_mod.hop_via(graph, mid, dst, 2),
    ]


def why_text(via, seed, dst, graph):
    sources = []
    for hop in via:
        for s in hop.get("sources") or []:
            if s not in sources:
                sources.append(s)
    if not sources:
        return ""
    nodes = graph.get("nodes") or {}
    nbr = (nodes.get(dst) or {}).get("name") or Path(dst).stem
    src = (nodes.get(seed) or {}).get("name") or Path(seed).stem
    cites = "、".join(f"[[{s}]]" for s in sources)
    return f"接続: [[{nbr}]] ← [[{src}]]、出典 {cites}"


def candidate(root, graph, seed, page, role, neigh):
    via = via_for(graph, seed, page, neigh)
    return {
        "page_path": page,
        "absolute_path": str((root / page).resolve()),
        "role": role,
        "via": via,
        "why": why_text(via, seed, page, graph),
    }


def sidecar_records_if_fresh(payload, built):
    """使える sidecar なら records。型が崩れていれば None（collect へ）。"""
    if not isinstance(payload, dict):
        return None
    gen = payload.get("generated_at")
    records = payload.get("records")
    if not isinstance(gen, str) or not isinstance(records, list):
        return None
    if not all(isinstance(rec, dict) for rec in records):
        return None
    if gen < built:
        return None
    return records


def load_contradiction_records(graph, root):
    sidecar = root / ".vault-meta" / "contradictions.json"
    built = graph.get("built_at") or ""
    if sidecar.is_file():
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        records = sidecar_records_if_fresh(payload, built)
        if records is not None:
            return records
    return contra_mod.collect(contra_mod.SCAN_DIRS, vault=root)


def contradiction_opponents(seed, records, graph, by_name):
    stem = Path(seed).stem
    matched = []
    for rec in records:
        host = rec.get("host") or ""
        pages = list(rec.get("pages") or [])
        if host == seed or stem in pages:
            matched.append(rec)
    if not matched:
        return [], "no-callout"
    by_page = {}
    for rec in matched:
        host = rec.get("host") or ""
        pages = list(rec.get("pages") or [])
        if host == seed:
            raw_opponents = pages
        else:
            raw_opponents = [host]
        status = rec.get("status") or "open"
        for raw in raw_opponents:
            if raw == seed or raw == stem:
                continue
            path = raw if raw in (graph.get("nodes") or {}) else resolve_name(raw, graph, by_name)
            if not path or path == seed:
                continue
            prev = by_page.get(path)
            openish = status == "open" or (prev and prev["open"])
            by_page[path] = {"open": openish}
    if not by_page:
        return [], "no-opponent"
    ordered = sorted(by_page.items(), key=lambda kv: (0 if kv[1]["open"] else 1, kv[0]))
    return [p for p, _ in ordered], None


def is_survey(seed, graph):
    if seed.startswith("wiki/surveys/"):
        return True
    return ((graph.get("nodes") or {}).get(seed) or {}).get("type") == "survey"


def is_seed_entity(seed, graph, root, status_cache):
    node = (graph.get("nodes") or {}).get(seed) or {}
    return node.get("type") == "entity" and page_status(seed, root, status_cache) == "seed"


def cluster_label_parts(clusters, cid):
    if cid is None:
        return set()
    for rec in (clusters or {}).get("clusters") or []:
        if rec.get("id") == cid:
            return {p.strip() for p in (rec.get("label") or "").split(" / ") if p.strip()}
    return set()


def is_distant_label_dump(chosen, neigh, titles, label_parts):
    """衝突後がちょうど 2 件、すべて距離 2、題名集合が塊 label と一致するときだけ空にする。"""
    if len(chosen) != 2 or not label_parts:
        return False
    dists = [(neigh.get(p) or {}).get("dist") for p in chosen]
    return set(titles) == label_parts and all(d == 2 for d in dists)


def needle_match(needle, page, graph, root, title_cache):
    folded = (needle or "").casefold()
    if len(folded) < NEEDLE_MIN:
        return False
    base = Path(page).stem.casefold()
    title = page_title(page, graph, root, title_cache).casefold()
    return folded in base or folded in title


def related_for(seed, graph=None, clusters=None, records=None, root=None, use_cache=None):
    """(exit_code, payload)。種の判定は graph['nodes'] の path 完全一致だけ。"""
    root = Path(root or vault_root())
    seed = seed.strip()
    disk_path = graph is None and clusters is None and records is None
    cache_on = disk_path if use_cache is None else bool(use_cache) and disk_path
    if graph is None:
        graph = graph_mod.load_graph(root / ".vault-meta" / "graph.json")
    if not graph or seed not in (graph.get("nodes") or {}):
        return EXIT_MISSING, missing_payload(seed)

    if clusters is None:
        clusters = clusters_mod.load_clusters(root / ".vault-meta" / "clusters.json") or {}

    epoch = cache_mod.cache_epoch(graph, clusters)
    key = cache_mod.cache_key(seed, graph)
    if cache_on:
        hit = cache_mod.read_related_cache(root, key, seed, epoch)
        if hit is not None:
            return EXIT_OK, hit
    generation = cache_mod.read_generation(root)
    if records is None:
        records = load_contradiction_records(graph, root)

    nodes = graph["nodes"]
    by_name = by_name_map(graph)
    neigh = bfs_neighborhood(graph, seed)
    title_cache = {}
    status_cache = {}
    fm_cache = {}

    related_list = fm_resolved(seed, "related", graph, by_name, root, fm_cache)
    sources_list = fm_resolved(seed, "sources", graph, by_name, root, fm_cache)
    needle = page_title(seed, graph, root, title_cache)

    d1_concepts = [
        p for p, info in neigh.items()
        if info["dist"] == 1 and (nodes.get(p) or {}).get("type") == "concept"
    ]
    d2_concepts = [
        p for p, info in neigh.items()
        if info["dist"] == 2 and (nodes.get(p) or {}).get("type") == "concept"
    ]
    definition_pop = list(d1_concepts)
    if len(d1_concepts) < 2:
        definition_pop.extend(d2_concepts)

    d1_sources = [
        p for p, info in neigh.items()
        if info["dist"] == 1 and (nodes.get(p) or {}).get("type") == "source"
    ]
    evidence_pop = []
    for p in d1_sources + sources_list + related_list:
        if (nodes.get(p) or {}).get("type") == "source" and p not in evidence_pop:
            evidence_pop.append(p)

    contra_pop, contra_omit = contradiction_opponents(seed, records, graph, by_name)

    membership = (clusters or {}).get("membership") or {}
    seed_cid = (membership.get(seed) or {}).get("id")
    label_parts = cluster_label_parts(clusters, seed_cid)
    survey = is_survey(seed, graph)
    seed_ent = is_seed_entity(seed, graph, root, status_cache)

    cluster_pop = []
    cluster_omit_fixed = None
    if survey:
        cluster_omit_fixed = "not-on-backbone"
    elif seed_ent:
        cluster_omit_fixed = "seed"
    else:
        for p, info in neigh.items():
            if p == seed:
                continue
            if (nodes.get(p) or {}).get("type") != "concept":
                continue
            if (membership.get(p) or {}).get("id") != seed_cid or seed_cid is None:
                continue
            cluster_pop.append(p)

    related_rank = {p: i for i, p in enumerate(related_list)}
    sources_rank = {p: i for i, p in enumerate(sources_list)}

    def dist(p):
        return (neigh.get(p) or {}).get("dist", 99)

    def score(p):
        return (neigh.get(p) or {}).get("score", 0.0)

    def in_count(p):
        return (membership.get(p) or {}).get("in_count", 0)

    definition_pop.sort(key=lambda p: (related_rank.get(p, 10**9), -score(p), p))
    evidence_pop.sort(key=lambda p: (
        0 if needle_match(needle, p, graph, root, title_cache) else 1,
        sources_rank.get(p, 10**9),
        related_rank.get(p, 10**9),
        -score(p),
        p,
    ))
    cluster_pop.sort(key=lambda p: (dist(p), -in_count(p), p))

    populations = {
        "contradiction": contra_pop,
        "evidence": evidence_pop,
        "definition": definition_pop,
        "cluster": cluster_pop,
    }
    omit_if_empty = {
        "contradiction": contra_omit or "no-callout",
        "evidence": "no-source",
        "definition": "no-concept-neighbor",
        "cluster": cluster_omit_fixed or "no-nearby-cluster-peer",
    }

    roles = {r: [] for r in FILL_ORDER}
    omitted = []
    reserved = set()
    contra_pages = set()

    for role in FILL_ORDER:
        if role == "cluster" and cluster_omit_fixed:
            omitted.append({"role": "cluster", "reason": cluster_omit_fixed})
            continue
        residual = []
        for page in populations[role]:
            if role == "evidence" and page in contra_pages:
                residual.append(page)
                continue
            if page in reserved:
                continue
            residual.append(page)
        chosen = residual[:ROLE_CAP]
        if role == "cluster":
            titles = [page_title(p, graph, root, title_cache) for p in residual]
            if is_distant_label_dump(residual, neigh, titles, label_parts):
                chosen = []
            elif (
                len(residual) >= 3
                and is_distant_label_dump(
                    residual[:2], neigh, titles[:2], label_parts,
                )
            ):
                chosen = residual[2:2 + ROLE_CAP]
        if not chosen:
            omitted.append({"role": role, "reason": omit_if_empty[role]})
            continue
        for page in chosen:
            roles[role].append(candidate(root, graph, seed, page, role, neigh))
            reserved.add(page)
            if role == "contradiction":
                contra_pages.add(page)

    payload = {"seed": seed, "roles": roles, "omitted": omitted}
    if cache_on:
        cache_mod.write_related_cache(root, key, seed, epoch, payload, generation)
    return EXIT_OK, payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="ページを種にした配役つき近傍。問い入口は無い。")
    parser.add_argument("page", help="vault 相対パス。graph['nodes'] の完全一致")
    args = parser.parse_args(argv)
    seed = args.page.strip()
    if not seed:
        log("ERR: ページパスが空")
        return EXIT_USAGE
    code, payload = related_for(seed)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
